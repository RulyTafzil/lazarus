#     Lazarus - A fork of Dodo, a graphical, hackable email client based on notmuch
#     Copyright (C) 2021 - Aleks Kissinger
#     Copyright (C) 2025 - Ruly Tafzil
#
# This file is part of Lazarus
#
# Lazarus is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Lazarus is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Lazarus. If not, see <https://www.gnu.org/licenses/>.
from __future__ import annotations
from typing import Optional, List

from PyQt6.QtCore import *
from PyQt6.QtGui import QKeyEvent, QTextCursor
from PyQt6.QtWidgets import *
import email.utils
import email.policy
import email.message
import subprocess
import tempfile
import typing
import os

from . import app
from . import panel
from . import keymap
from . import settings
from . import util
from . import pgp_util
from . import signature
from . import editor as editor_mod
from . import address_completer
from . import mime_builder
from . import compose_threads

# gnupg is only needed for pgp/mime support, do not throw when not present
try:
    import gnupg
except ImportError as ex:
    pass


class ComposePanel(panel.Panel):
    """A panel for composing messages

    :param mode: Composition mode. Possible values are '', 'mailto', 'reply', 'replyall',
                 and 'forward'
    :param msg: A JSON message referenced in a reply or forward. If mode != '',
                this cannot be None.
    """

    def __init__(self, a: app.Dodo, mode: str='', msg: Optional[dict]=None,
                 parent: Optional[QWidget]=None):
        super().__init__(a, keep_open=False, parent=parent)
        self.set_keymap(keymap.compose_keymap)
        self.mode = mode
        self.msg = msg
        self.temp_dirs: List[str] = []

        # ── Structured compose data ──────────────────────────────────
        self._data = mime_builder.ComposeData()

        # Determine initial account
        if msg:
            senders = util.get_header_addresses(msg['headers'], ['From', 'Reply-To'])
            recipients = util.get_header_addresses(msg['headers'], ['To', 'Cc'])
            if isinstance(settings.email_address, dict):
                self.current_account = next(
                        (
                         util.email_smtp_account_index(m) for _, m in
                         recipients + senders if
                         util.email_smtp_account_index(m) is not None
                         ), 0)
            else:
                self.current_account = 0
        else:
            self.current_account = 0

        self.pgp_sign = self.gnupg_keyid() is not None
        self.pgp_encrypt = False

        # ── Signatures ───────────────────────────────────────────────
        self.signature_text: Optional[str] = None
        self.signature_html: Optional[str] = None
        if settings.use_signature:
            self.signature_text, self.signature_html = signature.load(
                self.account_name())

        # ── Build the layout ─────────────────────────────────────────
        self._build_ui()

        # ── Populate fields from mode ────────────────────────────────
        self._data.from_addr = self.email_address()

        if msg and mode == 'mailto':
            if 'To' in msg['headers']:
                self.to_field.setText(msg['headers']['To'])
            if 'Subject' in msg['headers']:
                self.subject_field.setText(msg['headers']['Subject'])
            self._insert_signature()

        elif msg and (mode == 'reply' or mode == 'replyall'):
            send_to = [(name, e) for name, e in senders + recipients
                       if not util.email_is_me(e)]
            if send_to:
                self.to_field.setText(email.utils.formataddr(send_to.pop(0)))
                if mode == 'replyall' and send_to:
                    cc_values = [email.utils.formataddr(pair) for pair in send_to]
                    self.cc_field.setText(', '.join(cc_values))

            if 'Subject' in msg['headers']:
                subject = msg['headers']['Subject']
                if subject[:3].upper() != 'RE:':
                    subject = 'RE: ' + subject
                self.subject_field.setText(subject)

            quoted = util.quote_body_text(msg)
            # Build body: [signature with its own leading blank line]
            #            [blank line]
            #            [quoted text]
            # When there is no signature, the body starts with a leading
            # newline so the cursor has room before the quoted text.
            sig_block = ''
            if self.signature_text:
                sig_block = '\n-- \n' + self.signature_text.rstrip('\n') + '\n'
            body = sig_block if sig_block else '\n'
            if quoted:
                body += '\n' + quoted
            body = body.rstrip('\n') + '\n'
            self.editor.setPlainText(body)
            self._sig_block = sig_block

        elif msg and mode == 'forward':
            if 'Subject' in msg['headers']:
                subject = msg['headers']['Subject']
                if subject[:3].upper() != 'FW:':
                    subject = 'FW: ' + subject
                self.subject_field.setText(subject)

            # If the message has attachments, dump to temp dir
            temp_dir, att = util.write_attachments(msg)
            if temp_dir:
                self.temp_dirs.append(temp_dir)
            for fi in att:
                self._add_attachment_file(fi)

            # Build body (same layout as reply).
            sig_block = ''
            if self.signature_text:
                sig_block = '\n-- \n' + self.signature_text.rstrip('\n') + '\n'
            fwd_text = '---------- Forwarded message ---------\n'
            for h in ['From', 'Date', 'Subject', 'To']:
                if h in msg['headers']:
                    fwd_text += f'{h}: {msg["headers"][h]}\n'
            fwd_text += '\n' + util.body_text(msg) + '\n'
            body = sig_block if sig_block else '\n'
            body += '\n' + fwd_text
            body = body.rstrip('\n') + '\n'
            self.editor.setPlainText(body)
            self._sig_block = sig_block

        else:
            self.to_field.setFocus()
            self._insert_signature()

        # Always start the cursor at the very first line, regardless of
        # mode — whether that's an empty document, before a signature,
        # or before a signature + quoted/forwarded text.
        # Deferred via singleShot(0) so it fires after the panel is
        # added to the QTabWidget/QSplitter and has real geometry.
        def _reset_cursor_to_top() -> None:
            self.editor.moveCursor(QTextCursor.MoveOperation.Start)
            self.editor.verticalScrollBar().setValue(0)
        QTimer.singleShot(0, _reset_cursor_to_top)

        self._sync_data_from_fields()
        self.editor_thread: Optional[compose_threads.EditorThread] = None
        self.sendmail_thread: Optional[compose_threads.SendmailThread] = None

        self.refresh()

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Construct the compose panel UI."""
        lay = self.layout()
        if lay is None:
            return
        lay.setSpacing(4)

        # --- Account bar ---
        self.account_label = QLabel()
        self.account_label.setStyleSheet(
            f'color: {settings.theme["fg_good"]}; font-weight: bold;')
        self.status_label = QLabel()
        lay.addWidget(self._make_header_bar())

        # --- From field (readonly) ---
        self.from_label = QLabel()
        self.from_label.setStyleSheet(
            f'color: {settings.theme["fg_dim"]}; padding: 2px 4px;')
        lay.addWidget(self.from_label)

        # --- To field ---
        self.to_field = QLineEdit()
        self.to_field.setPlaceholderText('To')
        self.to_field.setStyleSheet(self._field_style())
        self._to_completer = address_completer.AddressCompleter(self)
        self._to_completer.set_line_edit(self.to_field)
        lay.addWidget(self.to_field)

        # --- Cc field ---
        self.cc_field = QLineEdit()
        self.cc_field.setPlaceholderText('Cc')
        self.cc_field.setStyleSheet(self._field_style())
        self._cc_completer = address_completer.AddressCompleter(self)
        self._cc_completer.set_line_edit(self.cc_field)
        lay.addWidget(self.cc_field)

        # --- Subject field ---
        self.subject_field = QLineEdit()
        self.subject_field.setPlaceholderText('Subject')
        self.subject_field.setStyleSheet(self._field_style())
        lay.addWidget(self.subject_field)

        # --- Bcc field ---
        self.bcc_field = QLineEdit()
        self.bcc_field.setPlaceholderText('Bcc')
        self.bcc_field.setStyleSheet(self._field_style())
        self._bcc_completer = address_completer.AddressCompleter(self)
        self._bcc_completer.set_line_edit(self.bcc_field)
        lay.addWidget(self.bcc_field)

        # --- Editor ---
        self.editor = editor_mod.RichTextEditor(self)
        lay.addWidget(self.editor, stretch=1)

        # --- Attachment bar ---
        self.attachment_bar = QWidget()
        self.attachment_layout = QHBoxLayout()
        self.attachment_layout.setContentsMargins(0, 0, 0, 0)
        self.attachment_layout.setSpacing(4)
        self.attachment_bar.setLayout(self.attachment_layout)
        self.attachment_bar.hide()
        lay.addWidget(self.attachment_bar)

        # Intercept compose command keys before QTextEdit can eat them
        self.editor.installEventFilter(self)

    def _make_header_bar(self) -> QWidget:
        """Build the top bar showing account selector + PGP status."""
        bar = QWidget()
        hlay = QHBoxLayout()
        hlay.setContentsMargins(0, 0, 0, 0)
        bar.setLayout(hlay)
        hlay.addWidget(self.account_label)
        hlay.addStretch()
        hlay.addWidget(self.status_label)
        return bar

    def _field_style(self) -> str:
        """Return a stylesheet string for header line edits."""
        return (
            f'background-color: {settings.theme["bg"]};'
            f'color: {settings.theme["fg_bright"]};'
            f'border: 1px solid {settings.theme["bg_button"]};'
            f'border-radius: 3px;'
            f'padding: 3px 6px;'
            f'font-family: {settings.message_font};'
            f'font-size: {settings.message_font_size}pt;'
        )

    def insert_newline(self) -> None:
        """Insert a newline at the editor cursor position."""
        self.editor.insertPlainText('\n')

    # ── Panel interface ──────────────────────────────────────────────

    def title(self) -> str:
        return 'compose'

    def refresh(self) -> None:
        """Refresh the compose panel display."""
        # Account label
        if len(settings.smtp_accounts) > 1:
            parts = []
            for i, acct in enumerate(settings.smtp_accounts):
                if i == self.current_account:
                    parts.append(f'[{acct}]')
                else:
                    parts.append(f' {acct} ')
            self.account_label.setText(
                f'Account: {"".join(parts)}')
        else:
            self.account_label.setText('')

        # From label
        self.from_label.setText(f'From: {self.email_address()}')

        # Status
        pgp = []
        if self.pgp_sign:
            pgp.append('PGPSign')
        if self.pgp_encrypt:
            pgp.append('PGPEncrypt')
        pgp_str = '  '.join(pgp)
        self.status_label.setText(pgp_str)
        self.status_label.setStyleSheet(
            f'color: {settings.theme["fg"]}; font-style: italic;')

        super().refresh()

    def _sync_data_from_fields(self) -> None:
        """Pull values from UI fields into self._data.

        Does NOT call ``collect_inline_images`` — that rewrites the
        editor HTML and is only safe to call once, at send time.
        """
        self._data.from_addr = self.email_address()
        self._data.to = _parse_address_list(self.to_field.text())
        self._data.cc = _parse_address_list(self.cc_field.text())
        self._data.bcc = _parse_address_list(self.bcc_field.text())
        self._data.subject = self.subject_field.text()
        self._data.body_html = self.editor.body_html()
        self._data.body_text = self.editor.toPlainText()

    # ── Attachments ──────────────────────────────────────────────────

    def _add_attachment_file(self, path: str) -> None:
        """Add *path* to the attachment list and show a chip."""
        if path in self._data.attachments:
            return
        self._data.attachments.append(path)
        chip = self._make_attachment_chip(path)
        self.attachment_layout.addWidget(chip)
        self.attachment_bar.show()

    def _remove_attachment(self, path: str) -> None:
        """Remove *path* from the attachment list."""
        if path in self._data.attachments:
            self._data.attachments.remove(path)
        # Rebuild chips
        _clear_layout(self.attachment_layout)
        for p in self._data.attachments:
            self.attachment_layout.addWidget(self._make_attachment_chip(p))
        if not self._data.attachments:
            self.attachment_bar.hide()

    def _make_attachment_chip(self, path: str) -> QWidget:
        """Return a small chip widget showing the filename with an X button."""
        chip = QWidget()
        hlay = QHBoxLayout()
        hlay.setContentsMargins(4, 2, 4, 2)
        hlay.setSpacing(4)
        chip.setLayout(hlay)
        chip.setStyleSheet(
            f'background-color: {settings.theme["bg_button"]};'
            f'border-radius: 3px;'
            f'padding: 2px;'
        )

        label = QLabel(os.path.basename(path))
        label.setStyleSheet(f'color: {settings.theme["fg"]};')
        hlay.addWidget(label)

        btn = QPushButton('✕')
        btn.setFlat(True)
        btn.setFixedSize(18, 18)
        btn.setStyleSheet(
            f'color: {settings.theme["fg_dim"]}; border: none;'
            f'font-size: 10pt;')
        btn.clicked.connect(lambda: self._remove_attachment(path))
        hlay.addWidget(btn)

        return chip

    def attach_file(self) -> None:
        """Open a file picker and attach the selected file(s)."""
        if settings.file_picker_command is None:
            file_list, _ = QFileDialog.getOpenFileNames()
        else:
            fd, file = tempfile.mkstemp()
            cmd = settings.file_picker_command.format(tempfile=file)
            subprocess.run(cmd, shell=True)
            with open(file, 'r') as f1:
                file_list = f1.read().split('\n')
            os.remove(file)

        for att in file_list:
            if att != '':
                self._add_attachment_file(att)
        self.refresh()

    # ── Signatures ───────────────────────────────────────────────────

    def _insert_signature(self) -> None:
        """Insert the current account's plaintext signature.

        On the first call the signature is appended at the end of the
        document.  On subsequent calls (account switch) the old signature
        block is replaced in-place — preserving any quoted reply text
        that appears below it.
        """
        doc = self.editor.document()
        full_text = doc.toPlainText()

        new_block = ''
        if self.signature_text:
            # Leading newline ensures a blank line always separates
            # the user's text from the signature, in every context.
            new_block = '\n-- \n' + self.signature_text.rstrip('\n') + '\n'

        # If we have a cached block (even empty — meaning "no sig yet"),
        # replace it in-place so the signature always stays above quoted text.
        if getattr(self, '_sig_block', None) is not None:
            old_block = self._sig_block
            idx = full_text.find(old_block)
            if idx >= 0:
                cursor = QTextCursor(doc)
                cursor.setPosition(idx)
                cursor.setPosition(
                    idx + len(old_block),
                    QTextCursor.MoveMode.KeepAnchor)
                if new_block:
                    cursor.insertText(new_block)
                else:
                    cursor.removeSelectedText()
                self._sig_block = new_block
                return

        # No old signature — append at end.
        if not new_block:
            return
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if full_text and not full_text.endswith('\n'):
            cursor.insertText('\n')
        cursor.insertText(new_block)
        self._sig_block = new_block
        # textCursor() shares the document with the widget — inserting
        # through it pushes the widget's own cursor forward when it was
        # sitting at the insertion point.  This branch only ever runs
        # on an empty document (mailto / blank-compose), so force the
        # cursor back to the start explicitly.
        self.editor.moveCursor(QTextCursor.MoveOperation.Start)

    def _reload_signature(self) -> None:
        """Swap the signature when the account changes."""
        if settings.use_signature:
            self.signature_text, self.signature_html = signature.load(
                self.account_name())
        else:
            self.signature_text = None
            self.signature_html = None

    # ── Account switching ────────────────────────────────────────────

    def account_name(self) -> str:
        """Return the name of the current SMTP account."""
        if not settings.smtp_accounts:
            return 'default'
        return settings.smtp_accounts[self.current_account]

    def email_address(self) -> str:
        """Return the email address for the current account."""
        if isinstance(settings.email_address, dict):
            return settings.email_address[self.account_name()]
        else:
            return settings.email_address

    def gnupg_keyid(self) -> str | None:
        """Get the GPG key id for the current SMTP account."""
        if isinstance(settings.gnupg_keyid, dict):
            return settings.gnupg_keyid.get(self.account_name())
        else:
            return settings.gnupg_keyid

    def next_account(self) -> None:
        """Cycle to the next SMTP account."""
        old_email = self.email_address()
        self.current_account = (self.current_account + 1) % len(
            settings.smtp_accounts)
        if self.email_address() != old_email:
            self._data.from_addr = self.email_address()
        self.pgp_sign = self.gnupg_keyid() is not None
        self._reload_signature()
        self._insert_signature()
        self.refresh()

    def previous_account(self) -> None:
        """Cycle to the previous SMTP account."""
        old_email = self.email_address()
        self.current_account = (self.current_account - 1) % len(
            settings.smtp_accounts)
        if self.email_address() != old_email:
            self._data.from_addr = self.email_address()
        self.pgp_sign = self.gnupg_keyid() is not None
        self._reload_signature()
        self._insert_signature()
        self.refresh()

    # ── PGP toggles ──────────────────────────────────────────────────

    def toggle_pgp_sign(self) -> None:
        if not self.gnupg_keyid():
            return
        self.pgp_sign = not self.pgp_sign
        self.refresh()

    def toggle_pgp_encrypt(self) -> None:
        self.pgp_encrypt = not self.pgp_encrypt
        self.refresh()

    # ── External editor (escape hatch) ───────────────────────────────

    def edit_externally(self) -> None:
        """Open the current message in the external editor.

        Bound to the ``E`` key.  Dumps the editor content to a temp file,
        opens it in the configured editor, and reads the result back."""
        if self.editor_thread is not None:
            return

        self._sync_data_from_fields()
        # Build a raw message string for the external editor
        raw = self._build_raw_message_string()
        self.editor_thread = compose_threads.EditorThread(raw, self, parent=self)

        def done() -> None:
            if self.editor_thread:
                if not self.is_open:
                    self.app.message(
                        'Compose panel closed',
                        'Compose panel closed while editing, '
                        'email text saved in:\n    - {}'.format(
                            self.editor_thread.file))
                else:
                    # Parse the result back into the editor
                    self._load_from_raw_message(
                        self.editor_thread.raw_message_string)
                self.editor_thread.deleteLater()
                self.editor_thread = None
            self.refresh()
            self.app.raise_panel(self)

        self.editor_thread.finished.connect(done)
        self.editor_thread.start()

    def _build_raw_message_string(self) -> str:
        """Build a flat text representation for the external editor."""
        lines = []
        lines.append(f'From: {self._data.from_addr}')
        if self._data.to:
            lines.append(f'To: {", ".join(self._data.to)}')
        if self._data.cc:
            lines.append(f'Cc: {", ".join(self._data.cc)}')
        lines.append(f'Subject: {self._data.subject}')
        for p in self._data.attachments:
            lines.append(f'A: {p}')
        lines.append('')
        lines.append(self._data.body_text)
        return '\n'.join(lines)

    def _load_from_raw_message(self, raw: str) -> None:
        """Parse a flat text message back into the editor fields."""
        headers, body = util.separate_headers(raw)
        msg = email.message_from_string(raw, policy=email.policy.compat32)

        # Extract headers
        self.to_field.setText(msg.get('To', ''))
        self.cc_field.setText(msg.get('Cc', ''))
        self.subject_field.setText(msg.get('Subject', ''))

        # Extract attachments (A: pseudo-header)
        att_paths: list[str] = []
        for line in headers.splitlines():
            if line.startswith('A:'):
                att_paths.append(line[2:].strip())

        # Clear and rebuild attachments
        self._data.attachments.clear()
        _clear_layout(self.attachment_layout)
        for p in att_paths:
            self._add_attachment_file(p)

        # Set body into editor
        self.editor.clear()
        self.editor.insertPlainText(body)

    # ── Send ─────────────────────────────────────────────────────────

    def send(self) -> None:
        """Send the message asynchronously."""
        if self.sendmail_thread is not None:
            return

        self._sync_data_from_fields()

        # Collect inline images (rewrites file:// → cid: in editor HTML)
        self._data.inline_images = self.editor.collect_inline_images()
        self._data.body_html = self.editor.body_html()
        self._data.body_text = self.editor.body_text()

        self.status_label.setText('sending...')
        self.status_label.setStyleSheet(
            f'color: {settings.theme["fg_bright"]}; font-style: italic;')
        self.refresh()

        self.sendmail_thread = compose_threads.SendmailThread(self, parent=self)
        self.sendmail_thread.send_success = False

        def done() -> None:
            if self.sendmail_thread:
                success = self.sendmail_thread.send_success
                self.sendmail_thread.deleteLater()
                self.sendmail_thread = None
            else:
                success = False
            self.app.refresh_panels()
            if success and self.is_open:
                self.app.status_message('Email sent', 'info')
                self.app.close_panel(self)

        self.sendmail_thread.finished.connect(done)
        self.sendmail_thread.start()

    def escape_focus(self) -> None:
        """Toggle focus between the editor and the compose panel chrome.

        Bound to ``<escape>``.  While editing, all keys type text and only
        Ctrl chords trigger commands.  Press ``<escape>`` to move focus to
        the panel itself — then all compose command keys (``a``, ``p``,
        ``e``, ``w``, ``E``, ``[``, ``]``) work as plain keypresses.
        Press ``<escape>`` again to return to the editor.
        """
        if self.editor.hasFocus():
            self.setFocus()
        else:
            self.editor.setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Override to let certain keys pass through to child line edits
        before the panel keymap intercepts them.

        When a QLineEdit has focus and its completer popup is visible,
        ``<enter>`` and ``<tab>`` must reach the widget for the completer
        to accept a suggestion.
        """
        fw = self.focusWidget()
        if isinstance(fw, QLineEdit) and event.key() in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
            # Let the QLineEdit handle this directly
            QLineEdit.keyPressEvent(fw, event)
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Intercept compose command chords before QTextEdit consumes them.

        QTextEdit handles alphanumeric keys as text input, so Ctrl chords
        never reach the panel keymap.  This filter catches **only** keys
        with modifiers (Ctrl, Alt, Meta) plus ``<escape>`` and ``<enter>``
        — plain letters and symbols pass through to the editor as text.
        """
        if obj is self.editor and event.type() == QEvent.Type.KeyPress:
            key_event = typing.cast(QKeyEvent, event)
            mods = key_event.modifiers()
            modifier_held = bool(
                mods & (Qt.KeyboardModifier.ControlModifier
                        | Qt.KeyboardModifier.AltModifier
                        | Qt.KeyboardModifier.MetaModifier))
            k = util.key_string(key_event)
            if k and self.keymap and k in self.keymap:
                # Only intercept keys with modifiers, <escape>, or <enter>.
                # Plain letters/symbols must reach the editor as text.
                if modifier_held or key_event.key() in (
                        Qt.Key.Key_Escape, Qt.Key.Key_Return,
                        Qt.Key.Key_Enter):
                    from . import panel as panel_mod
                    panel_mod.Panel.keyPressEvent(self, key_event)
                    return True
        return super().eventFilter(obj, event)

    def set_status(self, status: str, color: str) -> None:
        """Set the status label text and color."""
        self.status_label.setText(status)
        self.status_label.setStyleSheet(
            f'color: {settings.theme[color]}; font-style: italic;')


# -----------------------------------------------------------------------
# Helper: parse comma-separated address list
# -----------------------------------------------------------------------

def _parse_address_list(text: str) -> list[str]:
    """Parse a comma-separated address string into a list of trimmed entries."""
    if not text.strip():
        return []
    return [a.strip() for a in text.split(',') if a.strip()]


def _clear_layout(layout: QLayout) -> None:
    """Remove all widgets from *layout*."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
