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

from PyQt6.QtCore import QEvent, QObject, QTimer, Qt
from PyQt6.QtGui import QFont, QFontMetrics, QKeyEvent, QTextCursor
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)
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
from . import style
from . import signature
from . import editor as editor_mod
from . import address_completer
from . import mime_builder
from . import compose_model
from . import compose_threads
from .protocols import PanelApp

# To/Cc/Bcc/Subject rows get this much extra right padding so their boxes
# end at the same x as the From dropdown, which reserves trailing space
# for the PGP/send status label (8px row spacing + ~7px empty-label width).
_FIELD_RIGHT_PAD = 17


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

        # Labels + field text use a slightly smaller size than the body so
        # the header rows read as chrome, not content.
        self._field_font_size = max(settings.message_font_size - 1, 8)
        # Widest field label, so every input box shares a left edge.
        fm = QFontMetrics(QFont(settings.message_font, self._field_font_size))
        self._label_width = max(
            fm.horizontalAdvance(t)
            for t in ('To:', 'Cc:', 'Bcc:', 'Subject:', 'From:')) + 8

        # ── Structured compose data ──────────────────────────────────
        self._data = mime_builder.ComposeData()

        # Determine initial account via model helper
        self.current_account = compose_model.account_for_message(msg) if msg else 0
        self.pgp_sign = self.gnupg_keyid() is not None
        self.pgp_encrypt = False

        # ── Signatures ───────────────────────────────────────────────
        self.signature_text: Optional[str] = None
        self.signature_html: Optional[str] = None
        # The sig text currently in the document ('' = none) and the
        # exact quoted/forwarded tail the seed generated — maintained by
        # _insert_signature, which places the block by structure, not by
        # scanning for quote markers.
        self._sig_block = ''
        self._quoted_tail = ''
        if settings.use_signature:
            self.signature_text, self.signature_html = signature.load(
                self.account_name())

        # ── Build the layout ─────────────────────────────────────────
        self._build_ui()

        # ── Populate fields from mode (via compose_model seeds) ───────
        self._data.from_addr = self.email_address()
        seed = None
        if msg and mode == 'mailto':
            seed = compose_model.build_mailto_seed(msg)
            self.to_field.setText(seed.to_text)
            self.subject_field.setText(seed.subject)
            self._insert_signature()
        elif msg and (mode == 'reply' or mode == 'replyall'):
            seed = compose_model.build_reply_seed(
                msg, to_all=(mode == 'replyall'))
            self.to_field.setText(seed.to_text)
            self.cc_field.setText(seed.cc_text)
            # reply-all populates Cc — reveal the hidden row so it's visible
            if seed.cc_text:
                self.cc_row.show()
            self.subject_field.setText(seed.subject)
            self.editor.setPlainText(seed.body)
            self._quoted_tail = seed.quoted_tail
            self._insert_signature()
        elif msg and mode == 'forward':
            seed = compose_model.build_forward_seed(msg)
            self.subject_field.setText(seed.subject)
            for d in seed.temp_dirs:
                self.temp_dirs.append(d)
            for fi in seed.attachments:
                self._add_attachment_file(fi)
            self.editor.setPlainText(seed.body)
            self._quoted_tail = seed.quoted_tail
            self._insert_signature()
        else:
            def _focus_to() -> None:
                try:
                    self.to_field.setFocus()
                except RuntimeError:
                    pass  # panel destroyed before the timer fired
            QTimer.singleShot(0, _focus_to)
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
            # Reply/forward/forward: panel (chrome) focused so the
            # compose keymap is active ([ ] account switch, etc.),
            # not the To field's address completer. Deferred so it
            # runs after add_panel's setFocus().
            if msg is not None and mode in ('reply', 'replyall', 'forward'):
                try:
                    self.setFocus()
                except RuntimeError:
                    pass  # panel destroyed before the timer fired
        QTimer.singleShot(0, _reset_cursor_to_top)

        self._sync_data_from_fields()
        self.sendmail_thread: Optional[compose_threads.SendmailThread] = None

        self.refresh()

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Construct the compose panel UI."""
        lay = self.layout()
        if not isinstance(lay, QVBoxLayout):
            return
        lay.setSpacing(4)
        # 4px breathing room above the From row, matching the inter-field
        # spacing (the From row is the first widget in the layout).
        lay.setContentsMargins(0, 4, 0, 0)

        # --- From row (top): account picker (dropdown) + PGP/send status ---
        # One item per smtp_accounts entry; selecting an item switches
        # account (same path as the [ / ] keys).
        self.status_label = QLabel()
        self.from_combo = QComboBox()
        self.from_combo.setStyleSheet(self._combo_style())
        for i in range(len(settings.smtp_accounts)):
            self.from_combo.addItem(self._account_display(i), i)
        self.from_combo.setCurrentIndex(self.current_account)
        self.from_combo.currentIndexChanged.connect(self._on_from_combo_changed)
        from_row = self._make_field_row('From:', self.from_combo)
        row_lay = from_row.layout()
        assert row_lay is not None
        row_lay.addWidget(self.status_label)
        lay.addWidget(from_row)

        # --- To field ---
        self.to_field = QLineEdit()
        self.to_field.setStyleSheet(self._field_style())
        self._to_completer = address_completer.AddressCompleter(self)
        self._to_completer.set_line_edit(self.to_field)
        lay.addWidget(self._make_field_row(
            'To:', self.to_field, right_pad=_FIELD_RIGHT_PAD))

        # --- Cc row (hidden by default; M-c reveals) ---
        self.cc_row = QWidget(self)
        cc_layout = QHBoxLayout(self.cc_row)
        cc_layout.setContentsMargins(0, 0, _FIELD_RIGHT_PAD, 0)
        cc_layout.setSpacing(8)
        self.cc_field = QLineEdit()
        self.cc_field.setStyleSheet(self._field_style())
        self._cc_completer = address_completer.AddressCompleter(self)
        self._cc_completer.set_line_edit(self.cc_field)
        cc_layout.addWidget(self._make_label('Cc:'))
        cc_layout.addWidget(self.cc_field, stretch=1)
        self.cc_row.hide()
        lay.addWidget(self.cc_row)

        # --- Bcc row (hidden by default; M-b reveals) ---
        self.bcc_row = QWidget(self)
        bcc_layout = QHBoxLayout(self.bcc_row)
        bcc_layout.setContentsMargins(0, 0, _FIELD_RIGHT_PAD, 0)
        bcc_layout.setSpacing(8)
        self.bcc_field = QLineEdit()
        self.bcc_field.setStyleSheet(self._field_style())
        self._bcc_completer = address_completer.AddressCompleter(self)
        self._bcc_completer.set_line_edit(self.bcc_field)
        bcc_layout.addWidget(self._make_label('Bcc:'))
        bcc_layout.addWidget(self.bcc_field, stretch=1)
        self.bcc_row.hide()
        lay.addWidget(self.bcc_row)

        # --- Subject field ---
        self.subject_field = QLineEdit()
        self.subject_field.setStyleSheet(self._field_style())
        lay.addWidget(self._make_field_row(
            'Subject:', self.subject_field, right_pad=_FIELD_RIGHT_PAD))

        # --- Editor toolbar + editor ---
        self.editor = editor_mod.RichTextEditor(self)
        self.format_bar = self.editor.formatting_toolbar()
        # Toolbar hugs its content, left-aligned — not full width.
        lay.addWidget(self.format_bar, 0, Qt.AlignmentFlag.AlignLeft)
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

    def _make_label(self, text: str) -> QLabel:
        """A right-aligned field label (fixed width so all input boxes
        share a left edge)."""
        lbl = QLabel(text)
        lbl.setFixedWidth(self._label_width)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight
                         | Qt.AlignmentFlag.AlignVCenter)
        lbl.setStyleSheet(
            f'color: {settings.theme["fg_dim"]};'
            f'font-family: {settings.message_font};'
            f'font-size: {self._field_font_size}pt;'
        )
        return lbl

    def _account_display(self, idx: int) -> str:
        """Dropdown text for account *idx*: its address (which already
        carries the display name), with the account name prefixed when
        several accounts share the same address so the choices stay
        distinguishable."""
        addr = compose_model.email_for_account(idx)
        others = [compose_model.email_for_account(j)
                  for j in range(len(settings.smtp_accounts)) if j != idx]
        if addr in others:
            name = compose_model.account_name(idx)
            return f'{name} · {addr}' if addr else name
        return addr or compose_model.account_name(idx)

    def _combo_style(self) -> str:
        """Field-matching stylesheet for the From account dropdown, with
        a NerdFont chevron rendered to a temp PNG as the arrow (Qt QSS
        cannot reference font glyphs directly)."""
        arrow = style.glyph_image('\uf078', 12, settings.theme['fg_dim'])
        return (
            f'QComboBox {{'
            f' background-color: {settings.theme["bg"]};'
            f' color: {settings.theme["fg_bright"]};'
            f' border: 1px solid {settings.theme["bg_button"]};'
            f' border-radius: 3px;'
            f' padding: 3px 6px;'
            f' font-family: {settings.message_font};'
            f' font-size: {self._field_font_size}pt; }}'
            f'QComboBox::drop-down {{ border: none; width: 20px; }}'
            f'QComboBox::down-arrow {{'
            f'  image: url({arrow});'
            f'  width: 12px; height: 12px; }}'
            f'QComboBox QAbstractItemView {{'
            f'  background-color: {settings.theme["bg"]};'
            f'  color: {settings.theme["fg"]};'
            f'  border: 1px solid {settings.theme["bg_button"]};'
            f'  selection-background-color:'
            f'    {settings.theme["bg_button"]};'
            f'  selection-color: {settings.theme["fg_bright"]}; }}'
        )

    def _make_field_row(self, label: str, field: QWidget,
                        right_pad: int = 2) -> QWidget:
        """A labeled input row: right-aligned label + field.

        *right_pad* is the row's right margin (2px default breathing
        room; text rows pass :data:`_FIELD_RIGHT_PAD` to align with the
        From dropdown's reserved status space).
        """
        row = QWidget()
        hlay = QHBoxLayout(row)
        hlay.setContentsMargins(0, 0, right_pad, 0)
        hlay.setSpacing(8)
        hlay.addWidget(self._make_label(label))
        hlay.addWidget(field, stretch=1)
        return row

    def _field_style(self) -> str:
        """Return a stylesheet string for header line edits."""
        return (
            f'background-color: {settings.theme["bg"]};'
            f'color: {settings.theme["fg_bright"]};'
            f'border: 1px solid {settings.theme["bg_button"]};'
            f'border-radius: 3px;'
            f'padding: 3px 6px;'
            f'font-family: {settings.message_font};'
            f'font-size: {self._field_font_size}pt;'
        )

    def insert_newline(self) -> None:
        """Insert a newline at the editor cursor position."""
        self.editor.insertPlainText('\n')

    # ── Panel interface ──────────────────────────────────────────────

    def title(self) -> str:
        return 'compose'

    def refresh(self) -> None:
        """Refresh the compose panel display."""
        # From / account picker needs no rebuild: the combo's items are
        # fixed at construction and its selection tracks current_account
        # (kept in sync by _cycle_account / the combo signal).

        # Status: PGP toggles (and transient send/error text from
        # send()).  Plaintext mode is indicated by the toolbar toggle.
        parts = []
        if self.pgp_sign:
            parts.append('PGPSign')
        if self.pgp_encrypt:
            parts.append('PGPEncrypt')
        self.status_label.setText('  '.join(parts))
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
        # Hidden Cc/Bcc rows are disregarded on send, even if they hold
        # content — the text is remembered and restored on re-reveal.
        self._data.cc = _parse_address_list(self.cc_field.text()) \
            if not self.cc_row.isHidden() else []
        self._data.bcc = _parse_address_list(self.bcc_field.text()) \
            if not self.bcc_row.isHidden() else []
        self._data.subject = self.subject_field.text()
        self._data.body_html = self.editor.body_html()
        self._data.body_text = self.editor.toPlainText()

    # -- hidden Cc / Bcc fields (M-c / M-b) ------------------------------

    def reveal_cc(self) -> None:
        """Toggle the Cc row: reveal and focus, or hide it again.

        Hidden Cc content is disregarded on send (see
        :meth:`_sync_data_from_fields`) but remembered — re-revealing
        restores what was typed.
        """
        if self.cc_row.isHidden():
            self.cc_row.show()
            self.cc_field.setFocus()
        else:
            self.cc_row.hide()

    def reveal_bcc(self) -> None:
        """Toggle the Bcc row (same semantics as :meth:`reveal_cc`)."""
        if self.bcc_row.isHidden():
            self.bcc_row.show()
            self.bcc_field.setFocus()
        else:
            self.bcc_row.hide()

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

    def _sig_html_block(self) -> str:
        """The signature as a rich-text fragment, or ''.

        ``-- `` is the conventional plaintext separator and is inserted
        literally, so the block's plain-text rendering (used to locate
        it on the next account switch) is the same as the plaintext
        block's.
        """
        if not self.signature_html:
            return ''
        return f'<br>-- <br>{self.signature_html}<br>'

    def _insert_signature(self) -> None:
        """Insert or replace the current account's signature block.

        The document is ``[user text][sig block][quoted tail]`` and the
        exact blocks are known (:func:`compose_model.sig_edit`), so no
        content-marker scanning is needed.  Called once at compose time
        and again on every account switch; preserves the user's cursor.

        Rich mode with an HTML signature inserts the HTML block (its
        plain-text rendering is the same as the plaintext block's, so
        replacement still finds it); plain mode — or no HTML file —
        inserts the plaintext block.
        """
        doc = self.editor.document()
        if doc is None:
            return

        rich = (not self.editor.plain_mode and bool(self.signature_html))
        if rich:
            # The block in the document renders from the HTML file, so
            # the replacement search key must match that rendering —
            # not the plaintext file, whose content may differ.
            sig_key: str = util.html_to_plain(self.signature_html or '')
        else:
            sig_key = self.signature_text or ''

        text = doc.toPlainText()
        start, end, pre, sig, post = compose_model.sig_edit(
            text, self._sig_block, sig_key, self._quoted_tail)
        if start == end and not pre and not sig and not post:
            return  # nothing to change

        old_pos = self.editor.textCursor().position()
        cursor = QTextCursor(doc)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        if pre:
            cursor.insertText(pre)
        if rich:
            cursor.insertHtml(self._sig_html_block())
        else:
            cursor.insertText(compose_model.sig_block_text(sig))
        if post:
            cursor.insertText(post)
        self._sig_block = sig

        # Restore the user's cursor, shifted by the edit's length delta:
        # positions before the edit are unchanged, positions inside it
        # clamp to the new block's end, positions after shift by the
        # plain-text delta.
        plain_repl = pre + compose_model.sig_block_text(sig) + post
        new_pos = old_pos
        if old_pos > end:
            new_pos = old_pos + (len(plain_repl) - (end - start))
        elif old_pos > start:
            new_pos = start + len(plain_repl)
        c = self.editor.textCursor()
        c.setPosition(min(new_pos, doc.characterCount() - 1))
        self.editor.setTextCursor(c)

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

    def _set_account(self, idx: int) -> None:
        """Switch to SMTP account *idx* (dropdown selection or [ / ]).

        Updates From/PGP/signature state.  No-ops when *idx* is already
        current so the combo's ``currentIndexChanged`` (fired when the
        selection is set programmatically) never double-switches.
        """
        if idx == self.current_account:
            return
        old_email = self.email_address()
        self.current_account = idx
        if self.email_address() != old_email:
            self._data.from_addr = self.email_address()
        self.pgp_sign = self.gnupg_keyid() is not None
        self._reload_signature()
        self._insert_signature()
        self.refresh()

    def _on_from_combo_changed(self, idx: int) -> None:
        """Account picked from the From dropdown."""
        self._set_account(idx)

    def _cycle_account(self, delta: int) -> None:
        """Cycle the SMTP account by *delta* (±1) and keep the dropdown
        selection in sync with the new account."""
        idx = (self.current_account + delta) % len(settings.smtp_accounts)
        self._set_account(idx)
        if hasattr(self, 'from_combo'):
            # Fires currentIndexChanged → _on_from_combo_changed →
            # _set_account, which no-ops (account already current).
            self.from_combo.setCurrentIndex(idx)

    def next_account(self) -> None:
        """Cycle to the next SMTP account."""
        self._cycle_account(1)

    def previous_account(self) -> None:
        """Cycle to the previous SMTP account."""
        self._cycle_account(-1)

    # ── PGP toggles ──────────────────────────────────────────────────

    def toggle_pgp_sign(self) -> None:
        if not self.gnupg_keyid():
            return
        self.pgp_sign = not self.pgp_sign
        self.refresh()

    def toggle_pgp_encrypt(self) -> None:
        self.pgp_encrypt = not self.pgp_encrypt
        self.refresh()

    def toggle_plain(self) -> None:
        """Toggle the editor between rich-text and plaintext compose.

        Mirrors the reading view's ``H`` (HTML ↔ plain) toggle.  Bound to
        ``H`` in the compose keymap (chrome focus) and the toolbar's
        plaintext button.
        """
        self.editor.toggle_plain()
        self.refresh()

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
            if not self.is_open:
                # The panel was closed while the send was in flight —
                # close_panel skipped deleteLater() to avoid killing the
                # running thread.  It has finished now, so delete here.
                self.deleteLater()
                return
            if success:
                self.app.status_message('Email sent', 'info')
                self.app.close_panel(self)
            elif error:
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
