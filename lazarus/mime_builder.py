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
"""Build MIME email messages from structured compose data.

:func:`build_message` takes a :class:`ComposeData` and returns a
MIME message ready to be piped into the sendmail command.  It handles:

* Plain-text-only messages (no HTML, no images)
* HTML messages with or without inline images (``multipart/related``)
* File attachments
* Plaintext fallback (always included for HTML messages)
"""

from __future__ import annotations
import os
import mimetypes
from dataclasses import dataclass, field
from typing import Dict, List

import email.message
import email.utils
import email.policy
import email.mime.base
import email.mime.multipart
import email.mime.text
import email.mime.image
import email.mime.application
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase


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
    user_agent: str = 'Lazarus'


def _guess_mime(path: str) -> tuple[str, str]:
    """Return ``(maintype, subtype)`` for *path*."""
    mime, _ = mimetypes.guess_type(path)
    if mime and '/' in mime:
        return tuple(mime.split('/', 1))  # type: ignore[return-value]
    return ('application', 'octet-stream')


def build_message(
    data: ComposeData,
) -> MIMEMultipart | email.message.EmailMessage:
    """Build a MIME message from *data*.

    Returns a :class:`~email.message.EmailMessage` for simple messages
    or a :class:`~email.mime.multipart.MIMEMultipart` for complex ones
    (HTML + inline images).  Both support ``.as_string()`` for piping
    to msmtp.
    """
    if not data.body_html and not data.body_text:
        data.body_text = ''

    # ------------------------------------------------------------------
    # Case 1: Plain text only
    # ------------------------------------------------------------------
    if not data.body_html and not data.inline_images:
        eml = email.message.EmailMessage(
            policy=email.policy.EmailPolicy(utf8=False))
        _set_headers(eml, data)
        eml.set_content(data.body_text or '')
        _add_attachments(eml, data)
        return eml

    # ------------------------------------------------------------------
    # Case 2: HTML, no inline images
    # ------------------------------------------------------------------
    if not data.inline_images:
        eml = email.message.EmailMessage(
            policy=email.policy.EmailPolicy(utf8=False))
        _set_headers(eml, data)
        # RFC 2046: multipart/alternative must list parts in increasing order
        # of preference, with the richest (HTML) LAST — receivers render the
        # last alternative they can parse.  Plain first, HTML last.
        eml.set_content(data.body_text or '', subtype='plain')
        eml.add_alternative(data.body_html or '', subtype='html')
        _add_attachments(eml, data)
        return eml

    # ------------------------------------------------------------------
    # Case 3: HTML + inline images
    #
    # Correct structure (RFC 2387):
    #   multipart/alternative
    #     text/plain
    #     multipart/related
    #       text/html
    #       image/png  (Content-ID: <...>)
    #
    # We use the older MIMEMultipart API because EmailMessage's
    # add_alternative() wraps sub-messages as message/rfc822 instead
    # of embedding them inline.
    # ------------------------------------------------------------------

    # Build the multipart/related part: HTML body + inline images
    related = email.mime.multipart.MIMEMultipart('related')
    related.attach(email.mime.text.MIMEText(
        data.body_html or '', 'html'))

    for cid, path in data.inline_images.items():
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            continue
        maintype, subtype = _guess_mime(path)
        with open(path, 'rb') as f:
            img_data = f.read()

        if maintype == 'image':
            img_part: MIMEBase = email.mime.image.MIMEImage(
                img_data, _subtype=subtype)
        else:
            img_part = email.mime.application.MIMEApplication(
                img_data, _subtype=subtype)
        img_part['Content-ID'] = f'<{cid}>'
        img_part['Content-Disposition'] = 'inline'
        related.attach(img_part)

    # Wrap in multipart/alternative: text/plain + multipart/related
    alt = email.mime.multipart.MIMEMultipart('alternative')
    alt.attach(email.mime.text.MIMEText(data.body_text or '', 'plain'))
    alt.attach(related)

    _set_headers(alt, data)
    _add_attachments(alt, data)
    return alt


def _set_headers(
    msg: MIMEMultipart | email.message.EmailMessage,
    data: ComposeData,
) -> None:
    """Set common headers on *msg*."""
    msg['From'] = data.from_addr
    if data.to:
        msg['To'] = ', '.join(data.to)
    if data.cc:
        msg['Cc'] = ', '.join(data.cc)
    if data.bcc:
        msg['Bcc'] = ', '.join(data.bcc)
    msg['Subject'] = data.subject
    msg['Date'] = email.utils.formatdate(localtime=True)
    msg['Message-ID'] = data.message_id or email.utils.make_msgid()
    msg['User-Agent'] = data.user_agent


def _add_attachments(
    msg: MIMEMultipart | email.message.EmailMessage,
    data: ComposeData,
) -> None:
    """Attach files from *data.attachments* to *msg*."""
    for att_path in data.attachments:
        att_path = os.path.expanduser(att_path)
        if not os.path.exists(att_path):
            continue
        maintype, subtype = _guess_mime(att_path)
        with open(att_path, 'rb') as f:
            att_data = f.read()

        if hasattr(msg, 'add_attachment'):
            msg.add_attachment(
                att_data,
                maintype=maintype,
                subtype=subtype,
                filename=os.path.basename(att_path),
            )
        else:
            # MIMEMultipart
            if maintype == 'image':
                part: MIMEBase = email.mime.image.MIMEImage(
                    att_data, _subtype=subtype)
            elif maintype == 'application':
                part = email.mime.application.MIMEApplication(
                    att_data, _subtype=subtype)
            else:
                part = email.mime.text.MIMEText(
                    att_data.decode('utf-8', errors='replace'), _subtype=subtype)
            part['Content-Disposition'] = (
                f'attachment; filename="{os.path.basename(att_path)}"')
            msg.attach(part)
