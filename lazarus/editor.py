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
    QColor, QFont, QImage, QKeyEvent, QTextBlockFormat, QTextImageFormat,
    QTextListFormat, QDragEnterEvent, QDropEvent,
)
from PyQt6.QtWidgets import (
    QButtonGroup, QColorDialog, QFileDialog, QFrame, QHBoxLayout, QTextEdit,
    QToolButton, QWidget,
)

from . import settings

logger = logging.getLogger(__name__)

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
        """
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

        Inline images become ``[Image: filename]`` placeholders.
        """
        doc = self.document()
        if doc is None:
            return ''
        plain = doc.toPlainText()

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
        """
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
        fmt = self.currentCharFormat()
        fmt.setFontWeight(
            700 if self.fontWeight() < 700 else 400)
        self.mergeCurrentCharFormat(fmt)

    def toggle_italic(self) -> None:
        fmt = self.currentCharFormat()
        fmt.setFontItalic(not self.fontItalic())
        self.mergeCurrentCharFormat(fmt)

    def toggle_underline(self) -> None:
        fmt = self.currentCharFormat()
        fmt.setFontUnderline(not self.fontUnderline())
        self.mergeCurrentCharFormat(fmt)

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

        def make(text: str, tip: str, *, checkable: bool = False,
                 slot: Callable[..., Any] | None = None) -> QToolButton:
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setStyleSheet(
                f'QToolButton {{ color: {settings.theme["fg"]};'
                f' border-radius: 3px; padding: 1px 5px; }}'
                f'QToolButton:checked {{'
                f'  background-color: {settings.theme["bg_button"]}; }}'
                f'QToolButton:hover {{'
                f'  background-color: {settings.theme["bg_alt"]}; }}')
            if slot is not None:
                b.clicked.connect(slot)
            hlay.addWidget(b)
            return b

        def separator() -> None:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.VLine)
            sep.setStyleSheet(f'color: {settings.theme["fg_dim"]};')
            hlay.addWidget(sep)

        # -- character formatting --------------------------------------
        bold = make('B', 'Bold (Ctrl+B)', checkable=True,
                    slot=self.toggle_bold)
        f = bold.font()
        f.setBold(True)
        bold.setFont(f)
        italic = make('I', 'Italic (Ctrl+I)', checkable=True,
                      slot=self.toggle_italic)
        f = italic.font()
        f.setItalic(True)
        italic.setFont(f)
        underline = make('U', 'Underline (Ctrl+U)', checkable=True,
                         slot=self.toggle_underline)
        f = underline.font()
        f.setUnderline(True)
        underline.setFont(f)

        # -- alignment (exclusive group) -------------------------------
        separator()
        align_group = QButtonGroup(self)
        align_group.setExclusive(True)
        align_buttons: list[tuple[QToolButton, Qt.AlignmentFlag]] = []
        for glyph, tip, flag in (
                ('L', 'Align left', Qt.AlignmentFlag.AlignLeft),
                ('C', 'Align center', Qt.AlignmentFlag.AlignHCenter),
                ('R', 'Align right', Qt.AlignmentFlag.AlignRight),
        ):
            b = make(glyph, tip, checkable=True)
            b.clicked.connect(
                lambda _checked=False, a=flag: self._set_alignment(a))
            align_group.addButton(b)
            align_buttons.append((b, flag))

        # -- lists -----------------------------------------------------
        separator()
        bullet = make('•', 'Bulleted list', checkable=True,
                      slot=lambda: self._toggle_list(
                          QTextListFormat.Style.ListDisc))
        numbered = make('1.', 'Numbered list', checkable=True,
                        slot=lambda: self._toggle_list(
                            QTextListFormat.Style.ListDecimal))

        # -- colour / image --------------------------------------------
        separator()
        self._color_btn = make('A', 'Text colour', slot=self._choose_text_color)
        self._image_btn = make('🖼', 'Insert image', slot=self._choose_image)

        self._fmt_buttons = {
            'bold': bold, 'italic': italic, 'underline': underline,
            'bullet': bullet, 'numbered': numbered,
        }
        self._align_buttons = align_buttons

        self.currentCharFormatChanged.connect(
            lambda _fmt: self._sync_format_buttons())
        self.cursorPositionChanged.connect(self._sync_format_buttons)
        return bar

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
            f'QToolButton {{ color: {fmt.foreground().color().name()};'
            f' border-radius: 3px; padding: 1px 5px; }}')

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
