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
"""Compatibility shim — re-exports the split helper modules.

``lazarus.util`` was previously a 609-line grab-bag covering HTML/text
rendering, mail-content, email/account helpers, header wrapping, and key
handling.  It has been split into focused modules, but this file keeps
every symbol importable from ``lazarus.util`` for backward compat so
``config.py`` example code and ``from lazarus.util import X`` imports
continue to work.

New code should import from the owning module directly:

* HTML/text:  :mod:`lazarus.html_utils` (linkify, colorize, html2text, …)
* Mail parts: :mod:`lazarus.mail_utils` (message_parts, body_text, write_attachments, …)
* Keys:       :mod:`lazarus.keys` (key_string, basic_keytab, keytab)
* This file:  email identity + message helpers (strip/parse addresses,
              header splitting, wrapping, message CSS)

A ``DeprecationWarning`` is not yet emitted to avoid spamming on every
start — clean imports at your leisure.
"""

from __future__ import annotations

import email.utils
import textwrap
from typing import List, Tuple, Dict, Optional, Iterable

from . import settings

# -- email / account helpers (owned here) ---------------------------------

def get_header_addresses(
    headers: Dict[str, str], header_keys: List[str]
) -> List[Tuple[str, str]]:
    """Extract realnames and email addresses from message headers."""
    header_values = [headers[key] for key in header_keys if key in headers]
    return email.utils.getaddresses(header_values)


def strip_email_address(e: str) -> str:
    """Strip the display name, leaving just the email address

    E.g. "First Last <me@domain.com>" -> "me@domain.com"
    """
    return email.utils.parseaddr(e)[1]


def email_is_me(e: str) -> bool:
    """Check whether the provided email is me

    This compares settings.email_address with the provided email, after calling
    :func:`strip_email_address` on both. This method is used e.g. by
    :class:`lazarus.compose.Compose` to filter out the user's own email when forming
    a "reply-to-all" message.
    """
    if isinstance(settings.email_address, dict):
        addresses = [
            strip_email_address(v) for v in settings.email_address.values()
        ]
    else:
        addresses = [email.utils.parseaddr(settings.email_address)[1]]

    return strip_email_address(e).casefold() in [a.casefold() for a in addresses]


def email_smtp_account_index(e: str) -> Optional[int]:
    """Index in settings.smtp_accounts of account having the provided email address

    This method is used e.g. by :class:`lazarus.compose.Compose` to autmatically
    select the account to be used when replying to a mail. It returns the index
    of first matching account or None if provided email does not match
    any smtp account.  """
    assert isinstance(settings.email_address, dict), settings.email_address
    return next(
            (i for i, acc in enumerate(settings.smtp_accounts) if
             strip_email_address(e).casefold() ==
             strip_email_address(settings.email_address[acc]).casefold()
             ), None)


def chop_s(s: str) -> str:
    if len(s) > 20:
        return s[0:20] + '...'
    else:
        return s


# -- header / wrapping / css helpers (owned here) --------------------------

def separate_headers(s: str) -> Tuple[str, str]:
    """Split a message into its header part and body part"""

    h = ''
    b = ''
    headers = True
    for line in s.splitlines():
        if headers and line == '':
            headers = False
        elif headers:
            h += line + '\n'
        else:
            b += line + '\n'
    return (h, b)


def wrap_message(s: str) -> str:
    """Hard wrap message body using :func:`~lazarus.settings.wrap_column`

    Wrap the body part of the message. Headers and quoted text are not affected.
    """

    headers, body = separate_headers(s)
    body_wrap = ''

    for line in body.splitlines():
        if line[0:1] == '>':
            body_wrap += line + '\n'
        else:
            body_wrap += textwrap.fill(line, width=settings.wrap_column) + '\n'

    return headers + '\n' + body_wrap


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


# -- re-exports from split modules (backward compat) ----------------------
# Prefer importing from the owning module directly in new code; these
# stay here so ``from lazarus.util import X`` keeps working.

from .keys import key_string, basic_keytab, keytab  # noqa: E402,I001
from .html_utils import (  # noqa: E402
    clean_html2html,
    w3m_html2text,
    linkify,
    html2html,
    html2text,
    simple_escape,
    decode_header,
    colorize_text,
)
from .mail_utils import (  # noqa: E402
    message_parts,
    is_attachment,
    find_content,
    body_text,
    body_html,
    quote_body_text,
    sanitize_filename,
    write_attachments,
)
