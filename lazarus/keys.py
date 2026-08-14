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
"""Key → string mapping and keytab tables.

Split from ``util.py`` so that key handling has a single owner and
``util`` can focus on mail/text helpers.  ``util.key_string`` remains as
a re-export for backward compat (see :mod:`lazarus.util`).
"""

from __future__ import annotations

from typing import Dict

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent

basic_keytab: Dict[int, str] = {
  Qt.Key.Key_0: '0',
  Qt.Key.Key_1: '1',
  Qt.Key.Key_2: '2',
  Qt.Key.Key_3: '3',
  Qt.Key.Key_4: '4',
  Qt.Key.Key_5: '5',
  Qt.Key.Key_6: '6',
  Qt.Key.Key_7: '7',
  Qt.Key.Key_8: '8',
  Qt.Key.Key_9: '9',
  Qt.Key.Key_Ampersand: '&',
  Qt.Key.Key_Apostrophe: '\'',
  Qt.Key.Key_Asterisk: '*',
  Qt.Key.Key_At: '@',
  Qt.Key.Key_Backslash: '\\',
  Qt.Key.Key_Bar: '|',
  Qt.Key.Key_BraceLeft: '{',
  Qt.Key.Key_BraceRight: '}',
  Qt.Key.Key_BracketLeft: '[',
  Qt.Key.Key_BracketRight: ']',
  Qt.Key.Key_Colon: ':',
  Qt.Key.Key_Comma: ',',
  Qt.Key.Key_Dollar: '$',
  Qt.Key.Key_Equal: '=',
  Qt.Key.Key_Exclam: '!',
  Qt.Key.Key_Greater: '>',
  Qt.Key.Key_Less: '<',
  Qt.Key.Key_Minus: '-',
  Qt.Key.Key_NumberSign: '#',
  Qt.Key.Key_ParenLeft: '(',
  Qt.Key.Key_ParenRight: ')',
  Qt.Key.Key_Percent: '%',
  Qt.Key.Key_Period: '.',
  Qt.Key.Key_Plus: '+',
  Qt.Key.Key_Question: '?',
  Qt.Key.Key_QuoteDbl: '"',
  Qt.Key.Key_QuoteLeft: '`',
  Qt.Key.Key_Semicolon: ';',
  Qt.Key.Key_Slash: '/',
  Qt.Key.Key_A: 'a',
  Qt.Key.Key_B: 'b',
  Qt.Key.Key_C: 'c',
  Qt.Key.Key_D: 'd',
  Qt.Key.Key_E: 'e',
  Qt.Key.Key_F: 'f',
  Qt.Key.Key_G: 'g',
  Qt.Key.Key_H: 'h',
  Qt.Key.Key_I: 'i',
  Qt.Key.Key_J: 'j',
  Qt.Key.Key_K: 'k',
  Qt.Key.Key_L: 'l',
  Qt.Key.Key_M: 'm',
  Qt.Key.Key_N: 'n',
  Qt.Key.Key_O: 'o',
  Qt.Key.Key_P: 'p',
  Qt.Key.Key_Q: 'q',
  Qt.Key.Key_R: 'r',
  Qt.Key.Key_S: 's',
  Qt.Key.Key_T: 't',
  Qt.Key.Key_U: 'u',
  Qt.Key.Key_V: 'v',
  Qt.Key.Key_W: 'w',
  Qt.Key.Key_X: 'x',
  Qt.Key.Key_Y: 'y',
  Qt.Key.Key_Z: 'z',
}

keytab: Dict[int, str] = {
  Qt.Key.Key_Enter: 'enter',
  Qt.Key.Key_Return: 'enter',
  Qt.Key.Key_Escape: 'escape',
  Qt.Key.Key_Tab: 'tab',
  Qt.Key.Key_Backtab: 'tab',
  Qt.Key.Key_Backspace: 'backspace',
  Qt.Key.Key_Delete: 'delete',
  Qt.Key.Key_Insert: 'insert',
  Qt.Key.Key_Home: 'home',
  Qt.Key.Key_End: 'end',
  Qt.Key.Key_Left: 'left',
  Qt.Key.Key_Up: 'up',
  Qt.Key.Key_Right: 'right',
  Qt.Key.Key_Down: 'down',
  Qt.Key.Key_PageUp: 'pageup',
  Qt.Key.Key_PageDown: 'pagedown',
  Qt.Key.Key_CapsLock: 'capslock',
  Qt.Key.Key_NumLock: 'numlock',
  Qt.Key.Key_ScrollLock: 'scrolllock',
  Qt.Key.Key_F1: 'f1',
  Qt.Key.Key_F2: 'f2',
  Qt.Key.Key_F3: 'f3',
  Qt.Key.Key_F4: 'f4',
  Qt.Key.Key_F5: 'f5',
  Qt.Key.Key_F6: 'f6',
  Qt.Key.Key_F7: 'f7',
  Qt.Key.Key_F8: 'f8',
  Qt.Key.Key_F9: 'f9',
  Qt.Key.Key_F10: 'f10',
  Qt.Key.Key_F11: 'f11',
  Qt.Key.Key_F12: 'f12',
  Qt.Key.Key_F13: 'f13',
  Qt.Key.Key_F14: 'f14',
  Qt.Key.Key_F15: 'f15',
  Qt.Key.Key_F16: 'f16',
  Qt.Key.Key_F17: 'f17',
  Qt.Key.Key_F18: 'f18',
  Qt.Key.Key_F19: 'f19',
  Qt.Key.Key_F20: 'f20',
  Qt.Key.Key_F21: 'f21',
  Qt.Key.Key_F22: 'f22',
  Qt.Key.Key_F23: 'f23',
  Qt.Key.Key_F24: 'f24',
  Qt.Key.Key_F25: 'f25',
  Qt.Key.Key_F26: 'f26',
  Qt.Key.Key_F27: 'f27',
  Qt.Key.Key_F28: 'f28',
  Qt.Key.Key_F29: 'f29',
  Qt.Key.Key_F30: 'f30',
  Qt.Key.Key_F31: 'f31',
  Qt.Key.Key_F32: 'f32',
  Qt.Key.Key_F33: 'f33',
  Qt.Key.Key_F34: 'f34',
  Qt.Key.Key_F35: 'f35',
  Qt.Key.Key_Menu: 'menu',
  Qt.Key.Key_Help: 'help',
  Qt.Key.Key_Space: 'space',
}


def key_string(e: QKeyEvent) -> str:
    """Convert a Qt keycode plus modifiers into a human readable/writable string

    :param e: a QKeyEvent
    :returns: a string representing e.key() and its modifiers
    """

    if e.key() in basic_keytab:
        cmd = basic_keytab[e.key()]
        shift_modifier = False
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier == Qt.KeyboardModifier.ShiftModifier:
            cmd = cmd.upper()
    elif e.key() in keytab:
        shift_modifier = True
        cmd = '<' + keytab[e.key()] + '>'
    else:
        return ''

    if shift_modifier and (e.modifiers() & Qt.KeyboardModifier.ShiftModifier == Qt.KeyboardModifier.ShiftModifier):
        cmd = 'S-' + cmd
    if e.modifiers() & Qt.KeyboardModifier.AltModifier == Qt.KeyboardModifier.AltModifier:
        cmd = 'M-' + cmd
    if e.modifiers() & Qt.KeyboardModifier.ControlModifier == Qt.KeyboardModifier.ControlModifier:
        cmd = 'C-' + cmd

    return cmd
