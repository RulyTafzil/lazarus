#     Lazarus - A fork of Dodo, a graphical, hackable email client based on notmuch
#     Copyright (C) 2021 - Aleks Kissinger
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
"""Compatibility shim — the desktop's public ``lazarus.util`` surface.

The headless helpers moved to the standalone NED package
(:mod:`ned.util`, :mod:`ned.html_utils`, :mod:`ned.mail_utils`); this
file re-exports them so ``from lazarus.util import X`` keeps working in
``config.py`` and desktop modules. Qt-dependent and theme-dependent
helpers stay desktop-owned here:

* Keys:        :mod:`lazarus.keys` (key_string, basic_keytab, keytab)
* Message CSS: ``make_message_css`` (needs ``lazarus.settings.theme``)
* Tag sort:    ``sort_tags`` (needs ``lazarus.settings.tag_order``)

A ``DeprecationWarning`` is not yet emitted to avoid spamming on every
start — clean imports at your leisure.
"""

from __future__ import annotations

from typing import Iterable

from . import settings as _laz_settings
import ned.util as _ned_util
import ned.compose_model as _ned_compose_model

# Desktop process wiring: the shared headless helpers (reply seeds, account
# matching, wrapping) must see the DESKTOP's own configuration
# (``lazarus.settings`` — loaded from ~/.config/lazarus/config.py), not the
# daemon's (``ned.settings`` — ~/.config/ned/config.py). The daemon process
# never imports these shims, so its copy of the modules stays on ned.settings.
_ned_util.settings = _laz_settings
_ned_compose_model.settings = _laz_settings

from ned.util import (  # noqa: F401  (re-exported for the public lazarus.util surface)
    get_header_addresses,
    strip_email_address,
    email_is_me,
    email_smtp_account_index,
    chop_s,
    separate_headers,
    wrap_message,
    w3m_html2text,
    linkify,
    html2html,
    html2text,
    html_to_plain,
    simple_escape,
    decode_header,
    colorize_text,
    message_parts,
    is_attachment,
    find_content,
    body_text,
    body_html,
    quote_body_text,
    sanitize_filename,
    write_attachments,
)

from . import settings
from .keys import key_string, basic_keytab, keytab  # noqa: F401


def make_message_css() -> str:
    """Fill placeholders in settings.message_css using the current theme
    and font settings."""

    d = settings.theme.copy()
    d["message_font"] = settings.message_font
    d["message_font_size"] = str(settings.message_font_size)
    return settings.message_css.format(**d)


def sort_tags(tags: Iterable[str]) -> list[str]:
    """Sort *tags* by :data:`~lazarus.settings.tag_order` priority, then
    alphabetically — the display order used in the search tag column and
    the thread header info."""
    priority = {t: i for i, t in enumerate(settings.tag_order)}
    return sorted(tags, key=lambda t: (priority.get(t, len(settings.tag_order)), t))


__all__ = [
    "get_header_addresses",
    "strip_email_address",
    "email_is_me",
    "email_smtp_account_index",
    "chop_s",
    "separate_headers",
    "wrap_message",
    "make_message_css",
    "sort_tags",
    "w3m_html2text",
    "linkify",
    "html2html",
    "html2text",
    "html_to_plain",
    "simple_escape",
    "decode_header",
    "colorize_text",
    "message_parts",
    "is_attachment",
    "find_content",
    "body_text",
    "body_html",
    "quote_body_text",
    "sanitize_filename",
    "write_attachments",
    "key_string",
    "basic_keytab",
    "keytab",
]