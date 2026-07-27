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

from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import *
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent

from . import keymap
from . import util
from . import settings


def _render_keymap(name: str, mp: dict) -> str:
    """Render a single keymap group as an HTML fragment."""
    s = f'<h2 style="margin-top: 0">{name}</h2>\n'
    s += f'<table style="font-family: {settings.search_font}; '
    s += f'font-size: {settings.search_font_size}pt; width: 100%">\n'
    for key, val in mp.items():
        desc = val[0] if isinstance(val, tuple) else '(no description)'
        s += (f'<tr>'
              f'<td width="80" style="color: {settings.theme["fg_bright"]}; '
              f'white-space: nowrap">{util.simple_escape(key)}</td>\n'
              f'<td style="color: {settings.theme["fg"]}">{desc}</td></tr>\n')
    s += '</table><br />\n'
    return s


def _split_dict(d: dict) -> tuple[dict, dict]:
    """Split a dict into two roughly equal halves by key count."""
    items = list(d.items())
    mid = len(items) // 2
    return dict(items[:mid]), dict(items[mid:])


class HelpWindow(QWidget):
    """A three-column window showing all keybindings"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle('Dodo - Help')

        layout = QHBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)
        self.setLayout(layout)

        global_a, global_b = _split_dict(keymap.global_keymap)

        columns = [
            [("Navigation", keymap.navigation_keymap),
             ("Compose view", keymap.compose_keymap),
             ("Command bar", keymap.command_bar_keymap)],
            [("Global", global_a)],
            [("Global", global_b)],
        ]

        for col_maps in columns:
            col_html = ''
            for name, mp in col_maps:
                col_html += _render_keymap(name, mp)

            browser = QTextBrowser()
            browser.setHtml(col_html)
            layout.addWidget(browser)

        self.resize(950, 780)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        """Handle key press

        If <escape> is pressed, exit, otherwise pass the keypress on."""

        if e.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)
