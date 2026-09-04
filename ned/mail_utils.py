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
"""Message-part / body / attachment helpers.

Split from ``util.py`` so mail-content helpers have a single owner.
``lazarus.util`` re-exports these for backward compat.
"""

from __future__ import annotations

import email.utils
import logging
import os
import os.path
import sys
import tempfile
from typing import Callable, Iterator, List, Optional, Tuple

from .html_utils import html2text

logger = logging.getLogger(__name__)


def message_parts(m: dict) -> Iterator[dict]:
    """Iterate over JSON message parts recursively, depth-first."""
    if 'body' in m:
        for part in m['body']:
            yield from message_parts(part)
    elif 'content' in m:
        yield m
        if isinstance(m['content'], list):
            for part in m['content']:
                yield from message_parts(part)
    else:
        yield m


def is_attachment(part: dict) -> bool:
    """True if *part* is an attachment (not a PGP sig, has filename)."""
    content_disposition = part.get("content-disposition")
    content_type = part.get("content-type")
    return (
        "filename" in part
        and content_type != "application/pgp-signature"
        and (
            content_disposition == "attachment"
            or (
                content_disposition is None
                and content_type == "application/octet-stream"
            )
        )
    )


def find_content(m: dict, content_type: str) -> List[str]:
    """Return the 'content' of every part with the given content-type."""
    return [part['content'] for part in message_parts(m)
            if 'content' in part and part.get('content-type', '').casefold() == content_type.casefold()]


def body_text(m: dict) -> str:
    """Get the body text of a message (plain, or HTML→plain fallback)."""
    tc = find_content(m, 'text/plain')
    if len(tc) != 0:
        return tc[0]
    hc = find_content(m, 'text/html')
    if len(hc) != 0:
        return html2text(hc[0])
    return ''


def body_html(m: dict) -> str:
    """Get the body HTML of a message."""
    hc = find_content(m, 'text/html')
    if len(hc) != 0:
        return hc[0]
    return ''


def quote_body_text(m: dict) -> str:
    """Return the body text with '>' prepended to each line + attribution."""
    text = body_text(m)
    if not text:
        return ''
    # Missing/malformed From or Date headers must not crash compose
    # (e.g. replying to a message without a Date header used to raise
    # KeyError inside build_reply_seed).
    name, addr = email.utils.parseaddr(m['headers'].get('From', ''))
    who = name if name else (addr or 'Sender')
    date_hdr = m['headers'].get('Date')
    if date_hdr:
        try:
            dt = email.utils.parsedate_to_datetime(date_hdr)
        except (TypeError, ValueError):
            dt = None
    else:
        dt = None
    if dt is not None:
        prefix = f'On {dt.strftime("%c")}, {who} wrote:\n'
    else:
        prefix = f'{who} wrote:\n'
    return ''.join([prefix] + [f'> {ln}\n' for ln in text.splitlines()])


def sanitize_filename(name: str) -> str:
    """Replace invalid filename characters and truncate to 255 bytes."""
    encoding = sys.getfilesystemencoding()
    name = name.encode(encoding, errors="replace").decode(encoding)

    if sys.platform.startswith("win"):
        bad_chars = '\\/:*?"<>|'
    elif sys.platform.startswith("darwin"):
        bad_chars = '/:'
    else:
        bad_chars = '/'

    for bad_char in bad_chars:
        name = name.replace(bad_char, "_")

    max_bytes = 255
    root, ext = os.path.splitext(name)
    root = root[:max_bytes - len(ext)]
    excess = len(os.fsencode(root + ext)) - max_bytes

    # The loops below drop ~excess/4 characters per pass rather than one
    # at a time: every character is at least one byte, so removing k
    # characters always shrinks the encoded size by >= k and we can never
    # overshoot the 255-byte limit — a handful of passes converges even
    # for names full of multi-byte characters.
    while excess > 0 and root:
        root = root[:(-excess // 4)]
        excess = len(os.fsencode(root + ext)) - max_bytes

    if not root:
        root = name[0]
        excess = len(os.fsencode(root + ext)) - max_bytes
        while excess > 0 and ext:
            ext = ext[:(-excess // 4)]
            excess = len(os.fsencode(root + ext)) - max_bytes
        assert ext, name

    return root + ext


def write_attachments(
    m: dict,
    fetch_part: Optional[Callable[[str, int], bytes]] = None,
) -> Tuple[str, List[str]]:
    """Write attachments to a temp dir; returns (temp_dir, [paths]).

    When fetch_part is provided, it is used to download attachment bytes
    (e.g. client-side via NedClient.get_part). Otherwise falls back to
    local notmuch.show_part in daemon/headless context.
    """
    if not m:
        return ('', [])
    temp_dir = tempfile.mkdtemp(prefix='lazarus-')
    file_paths: list[str] = []

    for part in message_parts(m):
        if is_attachment(part):
            try:
                if fetch_part is not None:
                    content = fetch_part(m["id"], int(part["id"]))
                else:
                    from . import notmuch
                    content = notmuch.show_part(int(part["id"]), m["id"])
            except Exception as e:
                logger.debug("Could not fetch part %s of %s: %s", part["id"], m["id"], e)
                continue
            filename = part["filename"]
            if not content:
                print(f"Ignoring attachment {filename}: Got empty contents")
                continue

            p = os.path.join(temp_dir, sanitize_filename(filename))
            with open(p, 'wb') as att:
                att.write(content)
            file_paths.append(p)

    if len(file_paths) == 0:
        os.rmdir(temp_dir)
        return ('', [])
    return (temp_dir, file_paths)
