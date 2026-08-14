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
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QFont

from . import settings

_fonts: dict[tuple[str, int], QFont] = {}
_colors: dict[tuple[int, str], QColor] = {}


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
