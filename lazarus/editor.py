#     Lazarus - A fork of Dodo, a graphical, hackable email client based on notmuch
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
"""Built-in rich-text email editor.

:class:`RichTextEditor` is a :class:`~PyQt6.QtWidgets.QTextEdit` subclass
that supports inline images via paste or drag-and-drop, formatting
shortcuts (Ctrl+B/I/U), and a compact :meth:`formatting_toolbar` strip.
It is the only compose editor — the external-editor escape hatch was
removed.
"""

from __future__ import annotations
import os
import uuid
import tempfile
import re
import logging
from typing import Callable, Optional, Dict, Any

from PyQt6.QtCore import Qt, QMimeData
from PyQt6.QtGui import (
    QFont, QImage, QKeyEvent, QTextBlockFormat, QTextCharFormat,
    QTextImageFormat, QTextListFormat, QDragEnterEvent, QDropEvent,
)
from PyQt6.QtWidgets import (
    QButtonGroup, QColorDialog, QFileDialog, QFrame, QHBoxLayout, QTextEdit,
    QToolButton, QWidget,
)

from . import settings
from . import style

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Toolbar button styling
# ---------------------------------------------------------------------------

def _toolbar_btn_qss(color_hex: str) -> str:
    """Stylesheet shared by the compose toolbar buttons.

    Text in *color_hex*, checked fill, hover fill, and a **dimmed
    disabled state** (plaintext mode) with no hover — the explicit
    ``color`` would otherwise override Qt's automatic disabled
    grey-out and the buttons would look active while inert.
    """
    return (
        f'QToolButton {{ color: {color_hex};'
        f' border-radius: 3px; padding: 1px 5px; }}'
        f'QToolButton:checked {{'
        f'  background-color: {settings.theme["bg_button"]}; }}'
        f'QToolButton:hover {{'
        f'  background-color: {settings.theme["bg_alt"]}; }}'
        f'QToolButton:disabled {{'
        f'  color: {style.disabled_foreground()}; }}'
        f'QToolButton:disabled:hover {{'
        f'  background-color: transparent; }}')

# ---------------------------------------------------------------------------
# Image size limits
# ---------------------------------------------------------------------------

MAX_IMAGE_WIDTH = 1200
"""Images wider than this are scaled down on insert."""

MAX_IMAGE_HEIGHT = 1200
"""Images taller than this are scaled down on insert."""


# ---------------------------------------------------------------------------
# RichTextEditor
# ---------------------------------------------------------------------------

class RichTextEditor(QTextEdit):
    """A QTextEdit tuned for email composition.

    Features
    --------
    * Paste or drag-and-drop images → inline ``<img>`` elements
    * Images stored in a temp directory; rewritten to ``cid:`` refs on send
    * Plaintext fallback via :func:`body_text`
    * Formatting shortcuts (Ctrl+B/I/U)

    Lifecycle
    ---------
    The editor owns a :class:`tempfile.TemporaryDirectory` for inline images.
    Call :func:`cleanup` when the compose panel is closed to remove them.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._temp_dir = tempfile.TemporaryDirectory(prefix='lazarus-edit-')
        self._images: Dict[str, str] = {}  # cid → filepath (populated on collect)
        self._image_counter = 0
        self._plain_mode = False

        # Appearance
        self.setStyleSheet(
            f'background-color: {settings.theme["bg"]};'
            f'color: {settings.theme["fg"]};'
            f'font-family: {settings.message_font};'
            f'font-size: {settings.message_font_size}pt;'
        )
        self.setAcceptRichText(True)
        self.setTabChangesFocus(False)
        self.setMinimumHeight(220)
        self.setMinimumWidth(220)

        # Allow drag-and-drop of images
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Remove the temp directory and all inline images."""
        try:
            self._temp_dir.cleanup()
        except OSError as e:
            logger.debug('editor cleanup: %s', e)

    def insert_image_from_file(self, path: str) -> None:
        """Insert an image from *path* inline at the cursor position.

        The file is copied into the editor's temp directory and inserted
        as ``<img src="file://...">``.  On send, these local references
        are collected and rewritten to ``cid:`` references.
        """
        img = QImage(path)
        if img.isNull():
            logger.warning('Cannot read image: %s', path)
            return
        self._insert_qimage(img, os.path.splitext(os.path.basename(path))[1])

    def insert_image_from_data(self, data: bytes, fmt: str = 'png') -> None:
        """Insert an image from raw bytes at the cursor position."""
        img = QImage()
        if not img.loadFromData(data):
            logger.warning('Cannot decode image from %d bytes', len(data))
            return
        self._insert_qimage(img, '.' + fmt)

    def body_html(self) -> str:
        """Return the editor content as HTML suitable for MIME encoding.

        The returned HTML still contains ``file://`` references for inline
        images.  Call :func:`collect_inline_images` first to build the
        ``cid`` map and rewrite the HTML.

        Returns ``''`` in plaintext mode, so :func:`build_message` takes
        its plaintext-only path.
        """
        if self._plain_mode:
            return ''
        html = self.toHtml()
        # Qt wraps the body in an <html><head>...</head><body>...</body></html>
        # structure.  For email, we only want the body contents.
        body_match = re.search(
            r'<body[^>]*>(.*)</body>', html, re.DOTALL | re.IGNORECASE)
        if body_match:
            return body_match.group(1).strip()
        return html

    def body_text(self) -> str:
        """Return a plaintext rendering of the editor content.

        Inline images become ``[Image: filename]`` placeholders; the
        object-replacement character Qt embeds for images is stripped.
        """
        doc = self.document()
        if doc is None:
            return ''
        plain = doc.toPlainText().replace('\ufffc', '')

        # Append image placeholders
        for cid, path in self._images.items():
            fname = os.path.basename(path)
            plain += f'\n[Image: {fname}]'

        return plain.strip()

    def collect_inline_images(self) -> Dict[str, str]:
        """Build the ``cid → filepath`` map for MIME construction.

        Scans the current HTML for ``file://`` image references, assigns
        a unique ``Content-ID`` to each, and returns the mapping.

        **Side effect**: the editor's internal HTML is rewritten so that
        ``file://...`` src attributes become ``cid:...`` references.
        (No-op in plaintext mode — nothing is rewritten there.)
        """
        if self._plain_mode:
            return {}
        self._images = {}
        self._image_counter = 0

        html = self.toHtml()

        def _replace_src(m: re.Match) -> str:
            filepath = m.group(1)
            # Strip file:// prefix if present
            if filepath.startswith('file://'):
                filepath = filepath[7:]
            if not os.path.exists(filepath):
                return m.group(0)  # leave unchanged

            cid = str(uuid.uuid4())[:8]
            content_id = f'{cid}@lazarus.inline'
            self._images[content_id] = filepath
            return f'src="cid:{content_id}"'

        html = re.sub(
            r'src="(?:file://)?(/[^"]+)"',
            _replace_src,
            html,
        )

        self.setHtml(html)
        return self._images

    # ------------------------------------------------------------------
    # Formatting shortcuts
    # ------------------------------------------------------------------

    def toggle_bold(self) -> None:
        if self._plain_mode:
            return
        fmt = self.currentCharFormat()
        fmt.setFontWeight(
            700 if self.fontWeight() < 700 else 400)
        self.mergeCurrentCharFormat(fmt)

    def toggle_italic(self) -> None:
        if self._plain_mode:
            return
        fmt = self.currentCharFormat()
        fmt.setFontItalic(not self.fontItalic())
        self.mergeCurrentCharFormat(fmt)

    def toggle_underline(self) -> None:
        if self._plain_mode:
            return
        fmt = self.currentCharFormat()
        fmt.setFontUnderline(not self.fontUnderline())
        self.mergeCurrentCharFormat(fmt)

    # ------------------------------------------------------------------
    # Plaintext mode
    # ------------------------------------------------------------------

    @property
    def plain_mode(self) -> bool:
        """True when composing in plaintext mode (no HTML part on send)."""
        return self._plain_mode

    def toggle_plain(self) -> bool:
        """Toggle between rich-text and plaintext composing.

        Enabling strips all formatting in place (the text is preserved
        losslessly); the toolbar formatting buttons are disabled while
        plain, and :meth:`body_html` / :meth:`collect_inline_images`
        no-op so the outgoing message has no HTML part.

        :returns: the new plaintext mode.
        """
        self._plain_mode = not self._plain_mode
        if self._plain_mode:
            # Reset the editor's char format BEFORE re-inserting the
            # text: setPlainText() inserts with the current format, so a
            # bold/italic cursor would otherwise re-format the whole
            # body when stripping.  (Drop Qt's object-replacement chars
            # for embedded images too — their filenames still surface as
            # [Image: …] placeholders via body_text().)
            self.setCurrentCharFormat(QTextCharFormat())
            self.setPlainText(self.toPlainText().replace('\ufffc', ''))
        if hasattr(self, '_fmt_buttons'):
            self._sync_format_buttons()
        return self._plain_mode

    # ------------------------------------------------------------------
    # Formatting toolbar
    # ------------------------------------------------------------------

    def formatting_toolbar(self) -> QWidget:
        """Build the compact formatting strip shown above the editor.

        Bold / Italic / Underline, alignment, bullet / numbered lists,
        text colour, and insert-image.  Buttons reflect the cursor's
        current character format (kept in sync via
        ``currentCharFormatChanged`` / ``cursorPositionChanged``).  All
        buttons are ``NoFocus`` so keyboard focus never leaves the
        editor; toolbar use is mouse-driven, and the existing
        Ctrl+B/I/U shortcuts keep working.
        """
        bar = QWidget()
        hlay = QHBoxLayout(bar)
        hlay.setContentsMargins(2, 2, 2, 2)
        hlay.setSpacing(2)

        # NerdFont glyphs for the buttons (Font Awesome codepoints —
        # present in every NerdFont), so the strip reads as icons.
        nerd = QFont(style.nerd_font_family())
        nerd.setPixelSize(13)

        def make(text: str, tip: str, *, checkable: bool = False,
                 slot: Callable[..., Any] | None = None) -> QToolButton:
            b = QToolButton()
            b.setText(text)
            b.setFont(nerd)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setStyleSheet(_toolbar_btn_qss(settings.theme['fg']))
            if slot is not None:
                b.clicked.connect(slot)
            hlay.addWidget(b)
            return b

        def separator() -> None:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.VLine)
            sep.setStyleSheet(f'color: {settings.theme["fg_dim"]};')
            hlay.addWidget(sep)

        # -- plaintext / HTML toggle (left) ----------------------------
        # Two buttons styled exactly like the toolbar buttons beside
        # them — the active mode gets the darker (checked) background.
        # Built as plain QWidgets: a QML Switch would need a QQuickWidget
        # scene and is the wrong shape (on/off, not two labelled options).
        self._plain_btn = make('Plaintext', 'Plaintext mode (H)',
                               checkable=True, slot=self._set_plain)
        self._html_btn = make('HTML', 'HTML mode (H)',
                              checkable=True, slot=self._set_rich)
        seg_font = QFont(settings.message_font)
        seg_font.setPixelSize(11)
        self._plain_btn.setFont(seg_font)
        self._html_btn.setFont(seg_font)
        seg_group = QButtonGroup(self)
        seg_group.setExclusive(True)
        seg_group.addButton(self._plain_btn)
        seg_group.addButton(self._html_btn)
        separator()

        # -- character formatting --------------------------------------
        bold = make('\uf032', 'Bold (Ctrl+B)', checkable=True,
                    slot=self.toggle_bold)
        italic = make('\uf033', 'Italic (Ctrl+I)', checkable=True,
                      slot=self.toggle_italic)
        underline = make('\uf0cd', 'Underline (Ctrl+U)', checkable=True,
                         slot=self.toggle_underline)

        # -- alignment (exclusive group) -------------------------------
        separator()
        align_group = QButtonGroup(self)
        align_group.setExclusive(True)
        align_buttons: list[tuple[QToolButton, Qt.AlignmentFlag]] = []
        for glyph, tip, flag in (
                ('\uf036', 'Align left', Qt.AlignmentFlag.AlignLeft),
                ('\uf037', 'Align center', Qt.AlignmentFlag.AlignHCenter),
                ('\uf038', 'Align right', Qt.AlignmentFlag.AlignRight),
        ):
            b = make(glyph, tip, checkable=True)
            b.clicked.connect(
                lambda _checked=False, a=flag: self._set_alignment(a))
            align_group.addButton(b)
            align_buttons.append((b, flag))

        # -- lists -----------------------------------------------------
        separator()
        bullet = make('\uf0ca', 'Bulleted list', checkable=True,
                      slot=lambda: self._toggle_list(
                          QTextListFormat.Style.ListDisc))
        numbered = make('\uf0cb', 'Numbered list', checkable=True,
                        slot=lambda: self._toggle_list(
                            QTextListFormat.Style.ListDecimal))

        # -- colour / image --------------------------------------------
        separator()
        self._color_btn = make('\uf1fc', 'Text colour',
                               slot=self._choose_text_color)
        self._image_btn = make('\uf03e', 'Insert image',
                               slot=self._choose_image)

        # Formatting buttons are disabled while composing in plaintext.
        self._format_buttons = [
            bold, italic, underline, bullet, numbered,
            *[b for b, _ in align_buttons],
            self._color_btn, self._image_btn,
        ]

        self._fmt_buttons = {
            'bold': bold, 'italic': italic, 'underline': underline,
            'bullet': bullet, 'numbered': numbered,
        }
        self._align_buttons = align_buttons

        self.currentCharFormatChanged.connect(
            lambda _fmt: self._sync_format_buttons())
        self.cursorPositionChanged.connect(self._sync_format_buttons)
        # Reflect the initial state immediately (HTML segment checked on
        # open, formatting buttons enabled) — no cursor event needed.
        self._sync_format_buttons()
        return bar

    def _set_plain(self) -> None:
        """Switch to plaintext compose (Plaintext segment click)."""
        if not self._plain_mode:
            self.toggle_plain()

    def _set_rich(self) -> None:
        """Switch to rich-text compose (HTML segment click)."""
        if self._plain_mode:
            self.toggle_plain()

    def _set_alignment(self, flag: Qt.AlignmentFlag) -> None:
        """Align the current paragraph and restore editor focus."""
        self.setAlignment(flag)
        self._sync_format_buttons()
        self.setFocus()

    def _toggle_list(self, style: QTextListFormat.Style) -> None:
        """Toggle the current block between *style* list and no list."""
        cursor = self.textCursor()
        lst = cursor.currentList()
        if lst is not None and lst.format().style() == style:
            cursor.beginEditBlock()
            lst.remove(cursor.block())
            cursor.endEditBlock()
            cursor.setBlockFormat(QTextBlockFormat())
        else:
            cursor.createList(style)

    def _choose_text_color(self) -> None:
        """Pick a text colour via a dialog and apply it to the selection."""
        color = QColorDialog.getColor(self.textColor(), self, 'Text colour')
        if color.isValid():
            self.setTextColor(color)
        self.setFocus()

    def _choose_image(self) -> None:
        """Insert an image file at the cursor via a file dialog."""
        path, _ = QFileDialog.getOpenFileName(
            self, 'Insert image', '',
            'Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.svg)')
        if path:
            self.insert_image_from_file(path)
        self.setFocus()

    def _sync_format_buttons(self) -> None:
        """Reflect the cursor's character format in the toolbar buttons."""
        fmt = self.currentCharFormat()
        self._fmt_buttons['bold'].setChecked(fmt.fontWeight() >= 700)
        self._fmt_buttons['italic'].setChecked(fmt.fontItalic())
        self._fmt_buttons['underline'].setChecked(fmt.fontUnderline())

        lst = self.textCursor().currentList()
        lst_style = lst.format().style() if lst is not None else None
        self._fmt_buttons['bullet'].setChecked(
            lst_style == QTextListFormat.Style.ListDisc)
        self._fmt_buttons['numbered'].setChecked(
            lst_style == QTextListFormat.Style.ListDecimal)

        align = self.alignment()
        for b, flag in self._align_buttons:
            b.setChecked(align & flag == flag)

        self._color_btn.setStyleSheet(
            _toolbar_btn_qss(fmt.foreground().color().name()))

        # Plaintext mode: highlight the matching segment and grey out
        # the formatting buttons (they are meaningless without rich text).
        self._plain_btn.setChecked(self._plain_mode)
        self._html_btn.setChecked(not self._plain_mode)
        for b in self._format_buttons:
            b.setEnabled(not self._plain_mode)

    # ------------------------------------------------------------------
    # Qt event overrides
    # ------------------------------------------------------------------

    def insertFromMimeData(self, source: QMimeData | None) -> None:
        """Intercept paste to handle image data."""
        if source is None:
            return
        if source.hasImage():
            img = source.imageData()
            if isinstance(img, QImage) and not img.isNull():
                self._insert_qimage(img, '.png')
                return

        if source.hasUrls():
            for url in source.urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    ext = os.path.splitext(path)[1].lower()
                    if ext in _IMAGE_EXTENSIONS:
                        self.insert_image_from_file(path)
                        continue
            # If any URL was handled as an image, stop here
            if source.hasImage():
                return

        # Fall through to default text/html handling
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        if event is None:
            return
        md = event.mimeData()
        if md is not None and (md.hasImage() or md.hasUrls()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
        md = event.mimeData()
        if md is None:
            return
        if md.hasImage():
            img = md.imageData()
            if isinstance(img, QImage) and not img.isNull():
                self._insert_qimage(img, '.png')
                event.acceptProposedAction()
                return

        if md.hasUrls():
            for url in md.urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    ext = os.path.splitext(path)[1].lower()
                    if ext in _IMAGE_EXTENSIONS:
                        self.insert_image_from_file(path)
            event.acceptProposedAction()
            return

        super().dropEvent(event)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        """Handle formatting shortcuts that aren't consumed by the panel
        keymap (the panel sees Ctrl+B/I/U before we do, so these only
        fire when the panel keymap doesn't bind them)."""
        if event is None:
            return
        mods = event.modifiers()
        ctrl = mods & Qt.KeyboardModifier.ControlModifier

        if ctrl:
            if event.key() == Qt.Key.Key_B:
                self.toggle_bold()
                return
            if event.key() == Qt.Key.Key_I:
                self.toggle_italic()
                return
            if event.key() == Qt.Key.Key_U:
                self.toggle_underline()
                return

        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _insert_qimage(self, img: QImage, ext: str) -> None:
        """Scale *img* if needed, save to temp dir, insert <img> tag."""
        # Scale down large images
        if img.width() > MAX_IMAGE_WIDTH or img.height() > MAX_IMAGE_HEIGHT:
            img = img.scaled(
                MAX_IMAGE_WIDTH, MAX_IMAGE_HEIGHT,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        # Save to temp dir
        self._image_counter += 1
        fname = f'img_{self._image_counter}{ext}'
        path = os.path.join(self._temp_dir.name, fname)
        img.save(path)

        # Insert
        cursor = self.textCursor()
        fmt = QTextImageFormat()
        fmt.setName(path)
        fmt.setWidth(img.width())
        fmt.setHeight(img.height())
        cursor.insertImage(fmt)


# ---------------------------------------------------------------------------
# Recognised image file extensions for drag-and-drop
# ---------------------------------------------------------------------------

_IMAGE_EXTENSIONS: set[str] = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
    '.svg', '.tiff', '.tif', '.ico',
}
