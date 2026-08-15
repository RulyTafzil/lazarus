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
"""Shared Qt style helpers for list/tree cell rendering.

Qt calls ``data()`` on every visible cell for every repaint, and the
list/tree models (search, thread, tag) used to build a fresh
``QFont``/``QColor`` per call with near-identical code.  Base fonts are
memoised per ``(family, size)`` and returned as cheap shared copies
(``QFont`` uses implicit sharing, so copying is a refcount bump);
theme colors are parsed once per theme dict.  The color cache is keyed
on ``id(settings.theme)``, so replacing the theme dict (e.g. from
``config.py``) re-builds automatically — no invalidation hook needed.

Also resolves the NerdFont family used for icon glyphs (toolbar,
dropdown arrow) and renders glyphs to small PNGs for QSS ``image``
use — Qt stylesheets cannot reference font glyphs directly.
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import (
    QColor, QFont, QFontDatabase, QPainter, QPixmap,
)

from . import settings

_fonts: dict[tuple[str, int], QFont] = {}
_colors: dict[tuple[int, str], QColor] = {}
_glyph_files: dict[tuple[str, int, str], str] = {}
_nerd_family: Optional[str] = None
_nerd_family_key: str = ''


def cell_font(family: str, size: int, *,
              bold: bool = False, italic: bool = False) -> QFont:
    """Return a copy of the cached base font with bold/italic applied.

    Callers may mutate the returned font freely — it is a shared-COW
    copy, so ``setBold``/``setItalic`` detach rather than corrupting the
    cached base.
    """
    key = (family, size)
    base = _fonts.get(key)
    if base is None:
        base = QFont(family, size)
        _fonts[key] = base
    font = QFont(base)
    font.setBold(bold)
    font.setItalic(italic)
    return font


def theme_color(name: str) -> QColor:
    """Return the cached theme color for *name*.

    Raises :class:`KeyError` if *name* is not a key of the current
    ``settings.theme`` dict — callers that allow missing colors should
    check membership first (see ``search.render_thread_cell``).
    """
    key = (id(settings.theme), name)
    color = _colors.get(key)
    if color is None:
        color = QColor(settings.theme[name])
        _colors[key] = color
    return color


def nerd_font_family() -> str:
    """Resolve the NerdFont family used for icon glyphs.

    ``settings.nerd_font`` wins if set; otherwise the first installed
    family whose name contains ``'Nerd Font'``; finally ``tag_font``
    (whose private-use-area glyphs render via Qt font fallback anyway).
    Cached, and re-resolved if ``settings.nerd_font`` changes.
    """
    global _nerd_family, _nerd_family_key
    if _nerd_family is None or _nerd_family_key != settings.nerd_font:
        family: Optional[str] = settings.nerd_font or None
        if not family:
            for fam in QFontDatabase.families():
                if 'Nerd Font' in fam:
                    family = fam
                    break
        if not family:
            family = settings.tag_font
        _nerd_family = family
        _nerd_family_key = settings.nerd_font
    return _nerd_family or settings.tag_font


def glyph_image(glyph: str, size: int, color_hex: str) -> str:
    """Render *glyph* to a small PNG and return its path for QSS
    ``image: url(...)`` use — Qt stylesheets have no data URIs.

    Rendered once per (glyph, size, color); the file lives in the
    system temp dir for the session.
    """
    key = (glyph, size, color_hex)
    path = _glyph_files.get(key)
    if path is None:
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        font = QFont(nerd_font_family())
        font.setPixelSize(size - 2)
        painter.setFont(font)
        painter.setPen(QColor(color_hex))
        painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
        painter.end()
        fd, path = tempfile.mkstemp(prefix='lazarus-glyph-', suffix='.png')
        os.close(fd)
        pm.save(path)
        _glyph_files[key] = path
    return path
