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
"""HTML / text rendering helpers (w3m, linkify, sanitize, colorize).

Split from ``util.py`` so rendering helpers have a single owner.
``lazarus.util`` re-exports these for backward compat.
"""

from __future__ import annotations

import html
import re
import subprocess
import email.header

from bleach.linkifier import Linker  # type: ignore[import-untyped]


def w3m_html2text(s: str) -> str:
    """Convert HTML to plain text using "w3m -dump" """
    try:
        p = subprocess.run(
            ["w3m", "-T", "text/html", "-O", "utf8", "-dump"],
            stdout=subprocess.PIPE,
            encoding="utf8",
            input=s,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as e:
        return f"lazarus w3m error: {e}"
    return p.stdout


def linkify(s: str) -> str:
    """Link URLs and email addresses in *s* to HTML."""
    lnk = Linker()
    lnk_email = Linker(parse_email=True)
    return lnk_email.linkify(lnk.linkify(s))


def html2html(s: str) -> str:
    """Identity HTML filter — replace to customise HTML rendering."""
    return s


def html_to_plain(src: str) -> str:
    """Fast approximation of an HTML fragment's rendered text.

    Block-level tags and ``<br>`` become newlines, remaining tags are
    stripped, entities decoded.  Used to derive a plain-text search key
    for HTML signatures — the result matches Qt's ``toPlainText()``
    rendering of the inserted block closely enough to locate it.  Not a
    general HTML-to-text converter (see :func:`w3m_html2text`).
    """
    s = re.sub(r'(?i)<br\s*/?>', '\n', src)
    s = re.sub(r'(?i)</?(?:p|div|li|tr|h[1-6]|blockquote)\s*/?>', '\n', s)
    s = re.sub(r'<[^>]+>', '', s)
    return html.unescape(s).strip()


html2text = w3m_html2text
"""Function used to convert HTML to plain text (default: w3m)."""


def simple_escape(s: str) -> str:
    """Escape &, <, > for HTML."""
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def decode_header(s: str) -> str:
    """Decode any charset-encoded parts of an email header."""
    return str(email.header.make_header(email.header.decode_header(s)))


def colorize_text(s: str, has_headers: bool = False) -> str:
    """Add spans for quoted lines and headers (for use inside <pre>)."""

    s1 = ""
    quoted = re.compile(r'^\s*&gt;')
    empty = re.compile(r'^\s*$')

    headers = has_headers
    for ln in s.splitlines():
        if headers:
            if empty.match(ln):
                headers = False
                s1 += '\n'
            elif ':' in ln:
                parts = ln.split(':', 1)
                s1 += f'<span class="headername">{parts[0]}:</span>'
                s1 += f'<span class="headertext">{parts[1]}</span>\n'
            else:
                s1 += ln + '\n'
        else:
            if quoted.match(ln):
                s1 += f'<span class="quoted">{ln}</span>\n'
            else:
                s1 += ln + '\n'

    return s1
