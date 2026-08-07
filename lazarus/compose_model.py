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
"""Compose model — data + pure helpers extracted from ComposePanel.

Before this module, ``lazarus.compose.ComposePanel`` was ~760 lines:
account/reply-forward/signature/PGP logic, widget wiring, and template
assembly all lived in one ``__init__`` / ``_build_ui``.  That made the
panel hard to test (needs QApp + QSettings) and to reuse (no headless
compose).

Now ``ComposeState`` (dataclass) owns the writable compose state and
``build_reply`` / ``build_forward`` / ``build_mailto`` are the only
places that derive seed values from a ``msg`` dict.  They are pure
functions of ``msg`` + ``settings``, so they can be exercised without Qt.

``ComposePanel`` becomes a thin binding over this model:
``_data:ComposeData`` is the wire type for ``mime_builder``, while
``ComposeState`` captures account / sig / draft metadata that the panel
also needs.  Tests drive ``ComposeState`` and the builder functions
directly.
"""

from __future__ import annotations

import email.utils
from dataclasses import dataclass, field
from typing import Optional

from . import settings
from . import util

# ---------------------------------------------------------------------------
# Account helpers (moved from panel, now reusable)
# ---------------------------------------------------------------------------

def current_account_index() -> int:
    """Best-effort default account index from settings."""
    return 0


def account_for_message(msg: dict) -> int:
    """Pick an account for a reply/forward based on msg headers.

    Looks in From+Reply-To first, then To+Cc for our address; falls back
    to account 0 if nothing matches.  Mirrors the old ComposePanel.__init__
    heuristic exactly.
    """
    senders = util.get_header_addresses(msg.get('headers', {}), ['From', 'Reply-To'])
    recipients = util.get_header_addresses(msg.get('headers', {}), ['To', 'Cc'])
    if isinstance(settings.email_address, dict):
        for _, addr in recipients + senders:
            idx = util.email_smtp_account_index(addr)
            if idx is not None:
                return idx
    return 0


def account_name(idx: int) -> str:
    if not settings.smtp_accounts:
        return 'default'
    idx = max(0, min(idx, len(settings.smtp_accounts) - 1))
    return settings.smtp_accounts[idx]


def email_for_account(idx: int) -> str:
    name = account_name(idx)
    if isinstance(settings.email_address, dict):
        return settings.email_address.get(name, '')
    return settings.email_address  # type: ignore[return-value]


def gnupg_keyid_for_account(idx: int) -> str | None:
    name = account_name(idx)
    if isinstance(settings.gnupg_keyid, dict):
        return settings.gnupg_keyid.get(name)
    return settings.gnupg_keyid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Pure helpers (used by model builders & panel)
# ---------------------------------------------------------------------------

def sig_block_text(signature_text: Optional[str]) -> str:
    """Signature block with leading newline, or '' if no sig."""
    if not signature_text:
        return ''
    return '\n-- \n' + signature_text.rstrip('\n') + '\n'


def quote_body_text(msg: dict) -> str:
    """'On <date>, <name> wrote:' + '> ' lines, or ''."""
    return util.quote_body_text(msg)


def forwarded_text(msg: dict) -> str:
    """'---------- Forwarded message ---------' + headers + body."""
    t = '---------- Forwarded message ---------\n'
    for h in ['From', 'Date', 'Subject', 'To']:
        if h in msg.get('headers', {}):
            t += f'{h}: {msg["headers"][h]}\n'
    t += '\n' + util.body_text(msg) + '\n'
    return t


def normalize_body(body: str) -> str:
    """Ensure body ends with exactly one newline."""
    return body.rstrip('\n') + '\n'


def subject_with_prefix(subject: str, prefix: str) -> str:
    """Add RE:/FW: if not already there (case-insensitive)."""
    p = prefix.upper()
    if subject[:len(p) + 1].upper() != p + ':':
        return f'{prefix}: ' + subject
    return subject


def _parse_address_list(text: str) -> list[str]:
    if not text.strip():
        return []
    return [a.strip() for a in text.split(',') if a.strip()]


# ---------------------------------------------------------------------------
# Seed builders (pure, no Qt)
# ---------------------------------------------------------------------------

@dataclass
class ComposeSeed:
    """Values to prefill the compose panel with for a given mode/msg."""
    to_text: str = ''
    cc_text: str = ''
    subject: str = ''
    body: str = ''
    sig_block: str = ''  # cached sig block at seed time
    attachments: list[str] = field(default_factory=list)
    temp_dirs: list[str] = field(default_factory=list)


def build_mailto_seed(msg: dict, sig_text: Optional[str]) -> ComposeSeed:
    seed = ComposeSeed()
    headers = msg.get('headers', {})
    if 'To' in headers:
        seed.to_text = headers['To']
    if 'Subject' in headers:
        seed.subject = headers['Subject']
    seed.sig_block = sig_block_text(sig_text)
    # Body is just the signature block if any, else empty
    seed.body = seed.sig_block if seed.sig_block else ''
    return seed


def build_reply_seed(msg: dict, sig_text: Optional[str],
                    *, to_all: bool) -> ComposeSeed:
    seed = ComposeSeed()
    senders = util.get_header_addresses(msg.get('headers', {}), ['From', 'Reply-To'])
    recipients = util.get_header_addresses(msg.get('headers', {}), ['To', 'Cc'])
    send_to = [(n, e) for n, e in senders + recipients if not util.email_is_me(e)]
    if send_to:
        seed.to_text = email.utils.formataddr(send_to.pop(0))
        if to_all and send_to:
            seed.cc_text = ', '.join(email.utils.formataddr(p) for p in send_to)

    if 'Subject' in msg.get('headers', {}):
        seed.subject = subject_with_prefix(msg['headers']['Subject'], 'RE')

    seed.sig_block = sig_block_text(sig_text)
    quoted = quote_body_text(msg)
    body = seed.sig_block if seed.sig_block else '\n'
    if quoted:
        body += '\n' + quoted
    seed.body = normalize_body(body)
    return seed


def build_forward_seed(msg: dict, sig_text: Optional[str]) -> ComposeSeed:
    seed = ComposeSeed()
    if 'Subject' in msg.get('headers', {}):
        seed.subject = subject_with_prefix(msg['headers']['Subject'], 'FW')

    # Dump attachments to temp dir (side effect — keep here so seeds are useful off-thread)
    temp_dir, att = util.write_attachments(msg)
    if temp_dir:
        seed.temp_dirs.append(temp_dir)
    seed.attachments.extend(att)

    seed.sig_block = sig_block_text(sig_text)
    fwd = forwarded_text(msg)
    body = seed.sig_block if seed.sig_block else '\n'
    body += '\n' + fwd
    seed.body = normalize_body(body)
    return seed


def build_blank_seed(_sig_text: Optional[str]) -> ComposeSeed:
    # blank compose: just focus To, no body prefill
    return ComposeSeed()


# ---------------------------------------------------------------------------
# Runtime binding helpers
# ---------------------------------------------------------------------------

def to_field_to_data(to_text: str, cc_text: str, bcc_text: str, subject: str) -> tuple[list[str], list[str], list[str], str]:
    return (
        _parse_address_list(to_text),
        _parse_address_list(cc_text),
        _parse_address_list(bcc_text),
        subject,
    )
