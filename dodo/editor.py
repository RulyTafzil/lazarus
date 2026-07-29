#     Dodo - A graphical, hackable email client based on notmuch
#     Copyright (C) 2021 - Aleks Kissinger
#
# This file is part of Dodo
#
# Dodo is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Dodo is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Dodo. If not, see <https://www.gnu.org/licenses/>.

"""Built-in rich-text email editor.

:class:`RichTextEditor` is a :class:`~PyQt6.QtWidgets.QTextEdit` subclass
that supports inline images via paste or drag-and-drop.  It is the default
compose editor in Dodo, replacing the external-editor workflow while keeping
the external editor as an escape hatch (``E`` key).
"""

from __future__ import annotations
import os
import uuid
import tempfile
import re
import logging
from typing import Optional, Dict, List, Tuple

from PyQt6.QtCore import Qt, QMimeData, QUrl, QTimer
from PyQt6.QtGui import (
    QTextCursor, QTextImageFormat, QTextDocument, QImage,
    QKeyEvent, QPixmap, QDragEnterEvent, QDropEvent,
)
from PyQt6.QtWidgets import QTextEdit, QWidget, QApplication

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

        self._temp_dir = tempfile.TemporaryDirectory(prefix='dodo-edit-')
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
        self.setMinimumHeight(200)

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
            content_id = f'{cid}@dodo.inline'
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
    # Qt event overrides
    # ------------------------------------------------------------------

    def insertFromMimeData(self, source: QMimeData) -> None:
        """Intercept paste to handle image data."""
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

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasImage() or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        md = event.mimeData()
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

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle formatting shortcuts that aren't consumed by the panel
        keymap (the panel sees Ctrl+B/I/U before we do, so these only
        fire when the panel keymap doesn't bind them)."""

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
