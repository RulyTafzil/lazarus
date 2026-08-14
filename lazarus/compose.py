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
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .controller import AppController
    from .app import Dodo
from . import panel
from . import keymap
from . import settings
from . import util
from . import pgp_util
from . import signature
from . import editor as editor_mod
from . import address_completer
from . import mime_builder
from . import compose_model
from . import compose_threads
from .protocols import PanelApp

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
    # In the translucent-tab-bar prototype the panel itself must paint bg
    # so Compose's inter-field gaps (QVBoxLayout spacing with margins 0)
    # don't show desktop. Search/Thread use QTreeView/QWebEngine; Compose
    # is plain QWidgets so it needs an explicit fill (handled via Panel
    # autoFillBackground + themes QSS).

    def __init__(self, a: PanelApp, mode: str='', msg: Optional[dict]=None,
                 parent: Optional[QWidget]=None):
        super().__init__(a, keep_open=False, parent=parent)
        self.set_keymap(keymap.compose_keymap)
        self.mode = mode
        self.msg = msg
        self.temp_dirs: List[str] = []

        # ── Structured compose data ──────────────────────────────────
        self._data = mime_builder.ComposeData()

        # Determine initial account via model helper
        self.current_account = compose_model.account_for_message(msg) if msg else 0
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

        # ── Populate fields from mode (via compose_model seeds) ───────
        self._data.from_addr = self.email_address()
        seed = None
        if msg and mode == 'mailto':
            seed = compose_model.build_mailto_seed(msg, self.signature_text)
            self.to_field.setText(seed.to_text)
            self.subject_field.setText(seed.subject)
            # keep _insert_signature behavior for mailto
            self._insert_signature()
        elif msg and (mode == 'reply' or mode == 'replyall'):
            seed = compose_model.build_reply_seed(
                msg, self.signature_text, to_all=(mode == 'replyall'))
            self.to_field.setText(seed.to_text)
            self.cc_field.setText(seed.cc_text)
            self.subject_field.setText(seed.subject)
            self.editor.setPlainText(seed.body)
            self._sig_block = seed.sig_block
        elif msg and mode == 'forward':
            seed = compose_model.build_forward_seed(msg, self.signature_text)
            self.subject_field.setText(seed.subject)
            for d in seed.temp_dirs:
                self.temp_dirs.append(d)
            for fi in seed.attachments:
                self._add_attachment_file(fi)
            self.editor.setPlainText(seed.body)
            self._sig_block = seed.sig_block
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
            sb = self.editor.verticalScrollBar()
            if sb is not None:
                sb.setValue(0)
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

        # --- Account + From header row (header visual: bg_alt, inset 4px left) ---
        self.account_label = QLabel()
        self.account_label.setStyleSheet(
            f'color: {settings.theme["fg_good"]}; font-weight: bold;')
        self.status_label = QLabel()
        self.from_label = QLabel()
        # From reads like header text — fg_dim on header bg, same padding as QHeaderView::section
        self.from_label.setStyleSheet(
            f'color: {settings.theme["fg_dim"]}; padding: 2px 4px;')
        # Single row: account | From | status, with left inset matching QTreeView::item (4px)
        lay.addWidget(self._make_header_bar())

        # --- To field ---
        self.to_field = QLineEdit()
        self.to_field.setPlaceholderText('To')
        self.to_field.setStyleSheet(self._field_style())
        self._to_completer = address_completer.AddressCompleter(self)
        self._to_completer.set_line_edit(self.to_field)
        lay.addWidget(self.to_field)

        # --- Cc / Bcc row (side-by-side, 50% each) ---
        cc_bcc_row = QWidget(self)
        row_layout = QHBoxLayout(cc_bcc_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)
        self.cc_field = QLineEdit()
        self.cc_field.setPlaceholderText('Cc')
        self.cc_field.setStyleSheet(self._field_style())
        self._cc_completer = address_completer.AddressCompleter(self)
        self._cc_completer.set_line_edit(self.cc_field)
        self.bcc_field = QLineEdit()
        self.bcc_field.setPlaceholderText('Bcc')
        self.bcc_field.setStyleSheet(self._field_style())
        self._bcc_completer = address_completer.AddressCompleter(self)
        self._bcc_completer.set_line_edit(self.bcc_field)
        row_layout.addWidget(self.cc_field)
        row_layout.addWidget(self.bcc_field)
        lay.addWidget(cc_bcc_row)

        # --- Subject field ---
        self.subject_field = QLineEdit()
        self.subject_field.setPlaceholderText('Subject')
        self.subject_field.setStyleSheet(self._field_style())
        lay.addWidget(self.subject_field)

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
        """Build the top header row: Account | From | PGP status.

        Keeps the panel's normal bg (no header_bg override). Layout:
        4px left inset (matches QTreeView::item / QHeaderView::section),
        Account | From | stretch | PGP status.
        """
        bar = QWidget()
        hlay = QHBoxLayout()
        hlay.setContentsMargins(4, 2, 4, 2)
        bar.setLayout(hlay)
        hlay.addWidget(self.account_label)
        self._from_sep = QLabel(' · ')
        self._from_sep.setStyleSheet(f'color: {settings.theme["fg_dim"]};')
        hlay.addWidget(self._from_sep)
        hlay.addWidget(self.from_label)
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
            # file_picker_command is a shell command by contract
            # (documented as "{tempfile}" placeholder style).
            subprocess.run(cmd, shell=True)
            with open(file, 'r') as f1:
                file_list = f1.read().split('\n')
            os.remove(file)

        for att in file_list:
            if att != '':
                self._add_attachment_file(att)
        self.refresh()

    # ── Signatures ───────────────────────────────────────────────────

    def _sig_block_text(self) -> str:
        """Signature block with leading newline, or ''."""
        return compose_model.sig_block_text(self.signature_text)

    def _insert_signature(self) -> None:
        """Insert the current account's plaintext signature.

        On the first call the signature is appended at the end of the
        document.  On subsequent calls (account switch) the old signature
        block is replaced in-place — preserving any quoted reply text
        that appears below it.

        Bug fix: an empty ``_sig_block`` (no-sig account) previously
        matched at index 0 (``''.find('') == 0``) and caused the new sig
        to be inserted *before* user text. Empty old blocks are now
        treated as "no sig present" and the new sig is inserted after
        user text but before quoted/forwarded content when that exists.
        """
        doc = self.editor.document()
        if doc is None:
            return
        full_text = doc.toPlainText()

        new_block = self._sig_block_text()

        # In-place replacement only when we have a non-empty cached sig
        # that is actually found in the document.
        if getattr(self, '_sig_block', None) is not None:
            old_block = self._sig_block
            if old_block:
                idx = full_text.find(old_block)
                if idx >= 0:
                    old_pos = self.editor.textCursor().position()
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
                    len_diff = len(new_block) - len(old_block)
                    if old_pos > idx + len(old_block):
                        old_pos += len_diff
                    elif old_pos > idx:
                        old_pos = idx + len(new_block)
                    if self.editor.textCursor().position() != old_pos:
                        c = self.editor.textCursor()
                        c.setPosition(old_pos)
                        self.editor.setTextCursor(c)
                    return
                # Non-empty old sig not found (user deleted it) — fall
                # through to insertion logic below if we have a new sig.
                if not new_block:
                    self._sig_block = new_block
                    return
            else:
                # old_block == '' -> no previous sig
                if not new_block:
                    return
                # fall through to insertion

        # No old sig found (or no previous sig) — insert new sig if any.
        if not new_block:
            # Ensure cache is set for future switches
            if getattr(self, '_sig_block', None) is None:
                self._sig_block = ''
            return

        # Find quoted/forwarded block to insert *before* it, so the sig
        # stays above the quoted text. Otherwise append at end after user
        # text (the reported bug was sig inserted before user text).
        insert_idx = -1
        for marker in ("\nOn ", "---------- Forwarded message", "\n> "):
            idx = full_text.find(marker)
            if idx != -1:
                insert_idx = idx
                break

        cursor = QTextCursor(doc)
        if insert_idx != -1:
            cursor.setPosition(insert_idx)
            # new_block starts with "\n-- \n", so inserting at the
            # leading "\n" of the marker keeps correct spacing.
            # If the marker has no leading \n (forwarded at pos 0), just
            # insert there — leading \n in new_block still separates.
            if full_text and insert_idx == 0 and full_text.startswith("\n"):
                # Avoid doubling the leading newline when inserting at 0
                # (rare, but keeps formatting tidy).
                pass
            cursor.insertText(new_block)
        else:
            cursor = self.editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            if full_text and not full_text.endswith('\n'):
                cursor.insertText('\n')
            cursor.insertText(new_block)
        self._sig_block = new_block
        if not full_text.strip():
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
        return compose_model.account_name(self.current_account)

    def email_address(self) -> str:
        """Return the email address for the current account."""
        return compose_model.email_for_account(self.current_account)

    def gnupg_keyid(self) -> str | None:
        """Get the GPG key id for the current SMTP account."""
        return compose_model.gnupg_keyid_for_account(self.current_account)

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

        # Disable editing while the message is being sent.
        self._set_fields_enabled(False)
        self.status_label.setText('sending...')
        self.status_label.setStyleSheet(
            f'color: {settings.theme["fg_bright"]}; font-style: italic;')
        self.app.status_message('Sending...', 'info', duration=0)

        self.sendmail_thread = compose_threads.SendmailThread(self, parent=self)

        def done() -> None:
            # Re-enable fields regardless of outcome.
            self._set_fields_enabled(True)
            if self.sendmail_thread:
                success = self.sendmail_thread.send_success
                error = self.sendmail_thread.send_error
                self.sendmail_thread.deleteLater()
                self.sendmail_thread = None
            else:
                success = False
                error = ''
            self.app.refresh_panels()
            if success and self.is_open:
                self.app.status_message('Email sent', 'info')
                self.app.close_panel(self)
            elif error and self.is_open:
                self.status_label.setText(error)
                self.status_label.setStyleSheet(
                    f'color: {settings.theme["fg_bad"]};')
                self.app.status_message(f'Send failed: {error}', 'error')

        self.sendmail_thread.finished.connect(done)
        self.sendmail_thread.start()

    def _set_fields_enabled(self, enabled: bool) -> None:
        """Enable or disable all input fields and the editor."""
        self.editor.setEnabled(enabled)
        self.to_field.setEnabled(enabled)
        self.cc_field.setEnabled(enabled)
        self.bcc_field.setEnabled(enabled)
        self.subject_field.setEnabled(enabled)

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

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        """Override to let certain keys pass through to child line edits
        before the panel keymap intercepts them.

        When a QLineEdit has focus and its completer popup is visible,
        ``<enter>`` and ``<tab>`` must reach the widget for the completer
        to accept a suggestion.
        """
        if event is None:
            return
        fw = self.focusWidget()
        if isinstance(fw, QLineEdit) and event.key() in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
            # Let the QLineEdit handle this directly
            QLineEdit.keyPressEvent(fw, event)
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        """Intercept compose command chords before QTextEdit consumes them.

        QTextEdit handles alphanumeric keys as text input, so Ctrl chords
        never reach the panel keymap.  This filter catches **only** keys
        with modifiers (Ctrl, Alt, Meta) plus ``<escape>`` and ``<enter>``
        — plain letters and symbols pass through to the editor as text.
        """
        if obj is None or event is None:
            return super().eventFilter(obj, event)
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
        if item is None:
            continue
        w = item.widget()
        if w is not None:
            w.deleteLater()
