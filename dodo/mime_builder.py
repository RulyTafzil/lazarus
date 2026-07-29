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

"""Build MIME email messages from structured compose data.

:func:`build_message` takes a :class:`ComposeData` and returns a
:class:`~email.message.EmailMessage` ready to be piped into the
sendmail command.  It handles:

* Plain-text-only messages (no HTML, no images)
* HTML messages with or without inline images (``multipart/related``)
* File attachments
* Plaintext fallback (always included for HTML messages)
"""

from __future__ import annotations
import os
import mimetypes
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import email.message
import email.utils
import email.policy


@dataclass
class ComposeData:
    """Structured compose data — the single source of truth for the
    compose panel.

    Replaces the old ``raw_message_string`` pseudo-header approach.
    """

    from_addr: str = ''
    to: List[str] = field(default_factory=list)
    cc: List[str] = field(default_factory=list)
    bcc: List[str] = field(default_factory=list)
    subject: str = ''
    body_html: str = ''            # Rich-text body from the editor
    body_text: str = ''            # Plaintext fallback
    attachments: List[str] = field(default_factory=list)  # file paths
    inline_images: Dict[str, str] = field(default_factory=dict)  # cid → path
    message_id: str = ''
    in_reply_to: str = ''
    references: str = ''
    user_agent: str = 'Dodo'


def _guess_mime(path: str) -> tuple[str, str]:
    """Return ``(maintype, subtype)`` for *path*."""
    mime, _ = mimetypes.guess_type(path)
    if mime and '/' in mime:
        return tuple(mime.split('/', 1))  # type: ignore[return-value]
    return ('application', 'octet-stream')


def build_message(data: ComposeData) -> email.message.EmailMessage:
    """Build an :class:`~email.message.EmailMessage` from *data*.

    The returned message respects the email policy (UTF-8, 78-char line
    length) and is ready for sending via msmtp or similar.
    """
    eml = email.message.EmailMessage(
        policy=email.policy.EmailPolicy(utf8=False))

    # Headers
    eml['From'] = data.from_addr
    if data.to:
        eml['To'] = ', '.join(data.to)
    if data.cc:
        eml['Cc'] = ', '.join(data.cc)
    if data.bcc:
        eml['Bcc'] = ', '.join(data.bcc)
    eml['Subject'] = data.subject

    if data.message_id:
        eml['Message-ID'] = data.message_id
    else:
        eml['Message-ID'] = email.utils.make_msgid()

    eml['User-Agent'] = data.user_agent

    if not data.body_html and not data.body_text:
        data.body_text = ''

    # ------------------------------------------------------------------
    # Case 1: Plain text only (no HTML, no inline images)
    # ------------------------------------------------------------------
    if not data.body_html and not data.inline_images:
        eml.set_content(data.body_text or '')

    # ------------------------------------------------------------------
    # Case 2: HTML, no inline images
    # ------------------------------------------------------------------
    elif not data.inline_images:
        eml.set_content(data.body_html or '', subtype='html')
        eml.add_alternative(data.body_text or '', subtype='plain')

    # ------------------------------------------------------------------
    # Case 3: HTML + inline images → multipart/related
    # ------------------------------------------------------------------
    else:
        # Build the related sub-part
        related = email.message.EmailMessage(
            policy=email.policy.EmailPolicy(utf8=False))
        related.set_content(data.body_html or '', subtype='html')
        related.add_alternative(data.body_text or '', subtype='plain')

        for cid, path in data.inline_images.items():
            if not os.path.exists(path):
                continue
            maintype, subtype = _guess_mime(path)
            with open(path, 'rb') as f:
                img_data = f.read()
            related.add_attachment(
                img_data,
                maintype=maintype,
                subtype=subtype,
                cid=cid,
            )

        eml.set_content(related)

    # ------------------------------------------------------------------
    # File attachments
    # ------------------------------------------------------------------
    for att_path in data.attachments:
        if not os.path.exists(att_path):
            continue
        maintype, subtype = _guess_mime(att_path)
        with open(os.path.expanduser(att_path), 'rb') as f:
            att_data = f.read()
        eml.add_attachment(
            att_data,
            maintype=maintype,
            subtype=subtype,
            filename=os.path.basename(att_path),
        )

    return eml
