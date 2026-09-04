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
"""Compatibility shim — :mod:`ned.mail_utils` re-exported as ``lazarus.mail_utils``.

Mail-content helpers (message parts, bodies, attachments) live with the
standalone NED package; this shim keeps the desktop's public import names.
"""

from __future__ import annotations

from ned.mail_utils import (  # noqa: F401  (re-exported for desktop modules)
    message_parts,
    is_attachment,
    find_content,
    body_text,
    body_html,
    quote_body_text,
    sanitize_filename,
    write_attachments,
)

__all__ = [
    "message_parts",
    "is_attachment",
    "find_content",
    "body_text",
    "body_html",
    "quote_body_text",
    "sanitize_filename",
    "write_attachments",
]