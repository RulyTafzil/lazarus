#     Lazarus - A fork of Dodo, a graphical, hackable email client based on notmuch
#     Copyright (C) 2026 - Ruly Tafzil
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
"""Compatibility shim — :mod:`ned.html_utils` re-exported as ``lazarus.html_utils``.

HTML/text rendering helpers live with the standalone NED package; this
shim keeps the desktop's public import names.
"""

from __future__ import annotations

from ned.html_utils import (  # noqa: F401  (re-exported for desktop modules)
    linkify,
    colorize_text,
    w3m_html2text,
    html2html,
    html2text,
    html_to_plain,
    simple_escape,
    decode_header,
)

__all__ = [
    "linkify",
    "colorize_text",
    "w3m_html2text",
    "html2html",
    "html2text",
    "html_to_plain",
    "simple_escape",
    "decode_header",
]