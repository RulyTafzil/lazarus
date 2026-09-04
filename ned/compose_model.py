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


def sig_edit(body: str, old_sig: str, new_sig: str,
             quote_anchor: str) -> tuple[int, int, str, str, str]:
    """Decide the signature edit on *body*.

    The compose document is ``[user text][sig block][quoted tail]`` and
    the caller knows the exact blocks it inserted, so no content
    markers are needed.  Returns ``(start, end, pre, sig, post)``:
    replace ``[start, end)`` of *body* with ``pre + sig_block_text(sig)
    + post``, where *sig* is the bare signature text and ``pre``/``post``
    are separator newlines.  All-empty result means nothing to change.

    * ``old_sig``'s block still intact in *body* → replaced exactly.
    * ``old_sig`` missing (user edited/deleted it) → new sig inserted
      before *quote_anchor* (the exact quoted/forwarded text generated
      at compose time), or appended at the end when there is no anchor.
    * ``new_sig`` empty → removal only; an edited-away old block is left
      as the user's own text (never duplicated).

    Callers apply the edit with a ``QTextCursor`` — ``pre``/``post``
    stay plain text so the block's HTML form can be inserted instead of
    the plaintext one while the surrounding layout stays identical.
    """
    old_block = sig_block_text(old_sig)
    if old_block and old_block in body:
        start = body.find(old_block)
        end = start + len(old_block)
        if not new_sig:
            # Removal also drops the blank-line separator that follows
            # the block, so toggling between accounts with and without
            # signatures does not accumulate a stray newline per switch
            # (the next insert recreates the separator).
            if end < len(body) and body[end] == '\n':
                end += 1
        return start, end, '', new_sig, ''
    if not new_sig:
        return 0, 0, '', '', ''
    if quote_anchor and quote_anchor in body:
        idx = body.find(quote_anchor)
        return idx, idx, '', new_sig, '\n'
    # Append at the end, with a blank line before the block.
    end = len(body)
    if body and not body.endswith('\n'):
        return end, end, '\n', new_sig, ''
    return end, end, '', new_sig, ''


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
    quoted_tail: str = ''  # exact quoted/forwarded text (sig placement anchor)
    attachments: list[str] = field(default_factory=list)
    temp_dirs: list[str] = field(default_factory=list)


def build_mailto_seed(msg: dict) -> ComposeSeed:
    seed = ComposeSeed()
    headers = msg.get('headers', {})
    if 'To' in headers:
        seed.to_text = headers['To']
    if 'Subject' in headers:
        seed.subject = headers['Subject']
    return seed


def build_reply_seed(msg: dict, *, to_all: bool) -> ComposeSeed:
    seed = ComposeSeed()
    senders = util.get_header_addresses(msg.get('headers', {}), ['Reply-To', 'From'])
    recipients = util.get_header_addresses(msg.get('headers', {}), ['To', 'Cc'])

    if not to_all:
        external_senders = [(n, e) for n, e in senders if not util.email_is_me(e)]
        if external_senders:
            seed.to_text = email.utils.formataddr(external_senders[0])
        else:
            external_recipients = [(n, e) for n, e in recipients if not util.email_is_me(e)]
            if external_recipients:
                seed.to_text = email.utils.formataddr(external_recipients[0])
            elif senders:
                seed.to_text = email.utils.formataddr(senders[0])
            elif recipients:
                seed.to_text = email.utils.formataddr(recipients[0])
    else:
        send_to = [(n, e) for n, e in senders + recipients if not util.email_is_me(e)]
        if send_to:
            seed.to_text = email.utils.formataddr(send_to.pop(0))
            if send_to:
                seed.cc_text = ', '.join(email.utils.formataddr(p) for p in send_to)
        elif senders:
            seed.to_text = email.utils.formataddr(senders[0])

    if 'Subject' in msg.get('headers', {}):
        seed.subject = subject_with_prefix(msg['headers']['Subject'], 'RE')

    seed.quoted_tail = quote_body_text(msg)
    if seed.quoted_tail:
        # Two blank lines at the top of the body so there is room to
        # type above the quoted text (the cursor starts at the top).
        seed.body = '\n\n' + normalize_body(seed.quoted_tail)
    return seed


def build_forward_seed(msg: dict) -> ComposeSeed:
    seed = ComposeSeed()
    if 'Subject' in msg.get('headers', {}):
        seed.subject = subject_with_prefix(msg['headers']['Subject'], 'FW')

    # Dump attachments to temp dir (side effect — keep here so seeds are useful off-thread)
    temp_dir, att = util.write_attachments(msg)
    if temp_dir:
        seed.temp_dirs.append(temp_dir)
    seed.attachments.extend(att)

    fwd = forwarded_text(msg)
    seed.quoted_tail = fwd
    if fwd:
        # Two blank lines at the top of the body so there is room to
        # type above the forwarded text (the cursor starts at the top).
        seed.body = '\n\n' + normalize_body(fwd)
    return seed


