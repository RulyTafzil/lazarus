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
"""Core service logic for the Lazarus mobile web server.

Encapsulates notmuch queries, thread decomposition, tagging, Maildir moves,
contact lookups, and outbound email delivery without importing Qt.
"""

from __future__ import annotations

import email.parser
import email.utils
import json
import logging
import mailbox
import os
import re
import shlex
import subprocess
import tempfile
import threading
import urllib.parse
from typing import Any

from . import actions
from . import sync
from .. import compose_model
from .. import mail_utils
from .. import mime_builder
from .. import notmuch
from .. import settings
from .. import signature

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Contacts cache
# ---------------------------------------------------------------------------

_contacts_cache: list[dict[str, str]] = []
_contacts_lock = threading.Lock()
_contacts_loaded = False


def load_contacts(force_reload: bool = False) -> list[dict[str, str]]:
    """Load and cache unique contacts from notmuch address."""
    global _contacts_cache, _contacts_loaded
    with _contacts_lock:
        if _contacts_loaded and not force_reload:
            return _contacts_cache

        try:
            r = notmuch.run(
                'address',
                '--output=recipients',
                '--deduplicate=address',
                '--format=json',
                '--',
                '*',
                timeout=30,
            )
            if r.returncode == 0 and r.stdout.strip():
                raw = json.loads(r.stdout)
                cleaned: list[dict[str, str]] = []
                seen: set[str] = set()
                for item in raw:
                    addr = item.get('address', '').strip()
                    name = item.get('name', '').strip()
                    if not addr or addr.lower() in seen:
                        continue
                    seen.add(addr.lower())
                    display = f"{name} <{addr}>" if name else addr
                    cleaned.append({
                        'name': name,
                        'address': addr,
                        'display': display,
                    })
                _contacts_cache = cleaned
                _contacts_loaded = True
        except Exception as e:
            logger.warning('Failed to load contacts via notmuch address: %s', e)

        return _contacts_cache


def get_contacts(query: str = '') -> list[dict[str, str]]:
    """Return contacts matching substring in name or address."""
    all_contacts = load_contacts()
    q = query.strip().lower()
    if not q:
        return all_contacts[:50]
    matched: list[dict[str, str]] = []
    for c in all_contacts:
        if q in c['name'].lower() or q in c['address'].lower() or q in c['display'].lower():
            matched.append(c)
            if len(matched) >= 30:
                break
    return matched


# ---------------------------------------------------------------------------
# Search and Thread Views
# ---------------------------------------------------------------------------

def search_threads(query: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Query notmuch for thread summaries."""
    q = query.strip() or 'tag:inbox'
    try:
        r = notmuch.run(
            'search',
            '--format=json',
            f'--limit={limit}',
            f'--offset={offset}',
            '--',
            q,
            check=True,
        )
        if not r.stdout.strip():
            return []
        data = json.loads(r.stdout)
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        logger.warning('notmuch search failed for query %r: %s', q, e)
        return []


def search_messages(query: str, limit: int = 1000, offset: int = 0) -> list[str]:
    """Query notmuch for message IDs matching a query."""
    q = query.strip()
    try:
        r = notmuch.run(
            'search',
            '--exclude=false',
            '--format=json',
            '--output=messages',
            f'--limit={limit}',
            f'--offset={offset}',
            '--',
            q,
            check=True,
        )
        if not r.stdout.strip():
            return []
        data = json.loads(r.stdout)
        if isinstance(data, list):
            return [str(m) for m in data]
        return []
    except Exception as e:
        logger.warning('notmuch message search failed for query %r: %s', q, e)
        return []


def _flatten_messages(node: Any, output: list[dict[str, Any]]) -> None:
    """Recursively flatten notmuch show JSON tree into message list."""
    if isinstance(node, list):
        for item in node:
            _flatten_messages(item, output)
    elif isinstance(node, dict) and 'id' in node:
        output.append(node)


def get_thread_messages(thread_id: str, include_bodies: bool = True) -> dict[str, Any]:
    """Fetch all messages in a thread and extract clean display details."""
    clean_id = urllib.parse.unquote(thread_id).removeprefix('thread:')
    try:
        r = notmuch.run(
            'show',
            '--format=json',
            '--include-html',
            '--decrypt=true',
            '--',
            f'thread:{clean_id}',
            check=True,
        )
        raw_tree = json.loads(r.stdout)
    except Exception as e:
        logger.warning('Failed to fetch thread %r: %s', thread_id, e)
        return {'thread_id': clean_id, 'subject': '', 'tags': [], 'messages': [], 'tree': []}

    raw_messages: list[dict[str, Any]] = []
    _flatten_messages(raw_tree, raw_messages)

    messages: list[dict[str, Any]] = []
    all_tags: set[str] = set()
    subject = ''

    for m in raw_messages:
        headers = m.get('headers', {})
        msg_id = m.get('id', '')
        if not subject and headers.get('Subject'):
            subject = headers['Subject']

        tags = m.get('tags', [])
        all_tags.update(tags)

        if not include_bodies:
            continue

        # Body parts
        b_html = mail_utils.body_html(m)
        b_text = mail_utils.body_text(m)

        # Attachments
        attachments: list[dict[str, Any]] = []
        for part in mail_utils.message_parts(m):
            if mail_utils.is_attachment(part):
                filename = part.get('filename') or f"attachment-{part.get('id', 0)}"
                content_type = part.get('content-type', 'application/octet-stream')
                size = part.get('content-length') or 0
                attachments.append({
                    'part_id': part.get('id'),
                    'filename': filename,
                    'content_type': content_type,
                    'size': size,
                })

        messages.append({
            'id': msg_id,
            'subject': headers.get('Subject', '(no subject)'),
            'from': headers.get('From', ''),
            'to': headers.get('To', ''),
            'cc': headers.get('Cc', ''),
            'date': headers.get('Date', ''),
            'date_relative': m.get('date_relative', ''),
            'timestamp': m.get('timestamp', 0),
            'tags': tags,
            'body_html': b_html,
            'body_text': b_text,
            'attachments': attachments,
        })

    return {
        'thread_id': clean_id,
        'subject': subject or '(no subject)',
        'tags': sorted(list(all_tags)),
        'messages': messages,
        'tree': raw_tree,
    }


def get_part_data(message_id: str, part_id: int) -> tuple[bytes, str, str]:
    """Retrieve raw bytes for an attachment part."""
    clean_id = urllib.parse.unquote(message_id).removeprefix('id:').strip('<>')
    try:
        content = notmuch.show_part(part_id, clean_id, decrypt=True)
    except Exception as e:
        logger.warning('Failed to fetch part %d for message %r: %s', part_id, clean_id, e)
        return (b'', 'application/octet-stream', f'part-{part_id}')

    filename = f'part-{part_id}'
    content_type = 'application/octet-stream'

    # Try resolving filename and content type from show metadata
    try:
        r = notmuch.run(
            'show',
            '--format=json',
            '--decrypt=true',
            '--',
            f'id:{clean_id}',
            check=True,
        )
        data = json.loads(r.stdout)
        flat: list[dict[str, Any]] = []
        _flatten_messages(data, flat)
        if flat:
            for part in mail_utils.message_parts(flat[0]):
                if part.get('id') == part_id:
                    filename = part.get('filename') or filename
                    content_type = part.get('content-type') or content_type
                    break
    except Exception:
        pass

    return (content, content_type, filename)


# ---------------------------------------------------------------------------
# Tag and Thread Triage Actions
# ---------------------------------------------------------------------------

def modify_tags(ids: list[str], add_tags: list[str], remove_tags: list[str]) -> bool:
    """Add or remove tags on specified threads or message IDs."""
    if not ids:
        return True

    expr_parts: list[str] = []
    for t in add_tags:
        clean = t.strip().lstrip('+-')
        if clean:
            expr_parts.append(f"+{clean}")
    for t in remove_tags:
        clean = t.strip().lstrip('+-')
        if clean:
            expr_parts.append(f"-{clean}")

    if not expr_parts:
        return True

    tag_expr = ' '.join(expr_parts)
    query_parts: list[str] = []
    for item in ids:
        clean = urllib.parse.unquote(item.strip())
        if clean.startswith(('thread:', 'id:')):
            query_parts.append(clean)
        elif len(clean) == 16 and re.fullmatch(r'[0-9a-fA-F]+', clean):
            query_parts.append(f"thread:{clean}")
        else:
            query_parts.append(f"id:{clean}")

    query = ' or '.join(query_parts)
    try:
        r = notmuch.tag(tag_expr, query)
        return r.returncode == 0
    except Exception as e:
        logger.warning('notmuch tag failed for %r on %r: %s', tag_expr, query, e)
        return False


def archive_thread(thread_or_query: str) -> bool:
    """A archive: tag -inbox -unread, move files to local Archive Maildir, run notmuch new."""
    clean = urllib.parse.unquote(thread_or_query).strip()
    if clean.startswith("thread:") or " " in clean or ":" in clean:
        query = clean
    else:
        query = f"thread:{clean}"
    notmuch.tag("-inbox -unread", query)
    actions.move_to_archive(query)
    return True


def unarchive_thread(thread_or_query: str) -> bool:
    """Restore thread to inbox."""
    clean = urllib.parse.unquote(thread_or_query).strip()
    if clean.startswith("thread:") or " " in clean or ":" in clean:
        query = clean
    else:
        query = f"thread:{clean}"
    return modify_tags([query], add_tags=["inbox"], remove_tags=[])


def trash_thread(thread_or_query: str) -> bool:
    """Trash thread: tag +trash -inbox -unread, move files to account Trash, run notmuch new."""
    clean = urllib.parse.unquote(thread_or_query).strip()
    if clean.startswith("thread:") or " " in clean or ":" in clean:
        query = clean
    else:
        query = f"thread:{clean}"
    notmuch.tag("+trash -inbox -unread", query)
    actions.move_to_trash(query)
    return True


def untrash_thread(thread_or_query: str) -> bool:
    """Restore thread from trash to inbox."""
    clean = urllib.parse.unquote(thread_or_query).strip()
    if clean.startswith("thread:") or " " in clean or ":" in clean:
        query = clean
    else:
        query = f"thread:{clean}"
    actions.restore_from_trash(f"tag:trash AND ({query})")
    return modify_tags([query], add_tags=["inbox"], remove_tags=["trash"])


def expunge_trash() -> int:
    """Flag every file matching ``tag:trash`` with the Maildir T flag.

    Runs under the daemon's mutation lock (see ``ned.handler``) so it is
    serialized with the other single-writer mutations. Returns the number
    of files that were newly flagged.
    """
    return actions.expunge_trash()

def sync_mail() -> tuple[bool, str]:
    """Execute parallel mbsync per account (or sync_mail_command), run notmuch new, apply filter rules."""
    res = sync.run_sync()
    return (res.ok, res.message)


def toggle_flag(thread_id: str, flag: bool) -> bool:
    """Add or remove flagged tag from thread."""
    clean_id = urllib.parse.unquote(thread_id).removeprefix('thread:')
    if flag:
        return modify_tags([f"thread:{clean_id}"], add_tags=['flagged'], remove_tags=[])
    return modify_tags([f"thread:{clean_id}"], add_tags=[], remove_tags=['flagged'])


def get_all_tags() -> list[dict[str, Any]]:
    """Return all known tags with thread counts."""
    all_tags = sorted(notmuch.tags())
    queries = [f'tag:{t}' for t in all_tags]
    counts = notmuch.count_batch(queries, output='threads')
    return [{'name': t, 'count': c} for t, c in zip(all_tags, counts)]


def count_query(query: str, output: str = 'messages') -> int:
    """Return count of matching messages, threads, or files."""
    try:
        return notmuch.count(query, output=output)
    except Exception as e:
        logger.warning('Failed to count query %r: %s', query, e)
        return 0


def count_queries(queries: list[str], output: str = 'messages') -> list[int]:
    """Return count for each query in batch."""
    try:
        return notmuch.count_batch(queries, output=output)
    except Exception as e:
        logger.warning('Failed count batch: %s', e)
        return [0] * len(queries)


# ---------------------------------------------------------------------------
# Reply Seeds and Outbound Delivery
# ---------------------------------------------------------------------------

def get_reply_seed(message_id: str, to_all: bool = False) -> dict[str, Any]:
    """Build pre-populated reply data for a given message ID."""
    clean_id = urllib.parse.unquote(message_id).removeprefix('id:').strip('<>')
    try:
        r = notmuch.run(
            'show',
            '--format=json',
            '--include-html',
            '--decrypt=true',
            '--',
            f'id:{clean_id}',
            check=True,
        )
        raw_tree = json.loads(r.stdout)
        flat: list[dict[str, Any]] = []
        _flatten_messages(raw_tree, flat)
        if not flat:
            raise ValueError(f"Message {message_id} not found")
        msg = flat[0]
    except Exception as e:
        logger.warning('Failed to fetch reply target %r: %s', message_id, e)
        return {
            'to': '',
            'cc': '',
            'subject': 'RE: ',
            'body': '',
            'in_reply_to': f'<{clean_id}>',
            'references': f'<{clean_id}>',
        }

    seed = compose_model.build_reply_seed(msg, to_all=to_all)
    in_reply_to = f"<{clean_id}>"

    # Assemble References
    refs = [in_reply_to]
    if 'filename' in msg and msg['filename']:
        try:
            with open(msg['filename'][0], 'rb') as f:
                old_msg = email.parser.BytesParser().parse(f, headersonly=True)
                if 'References' in old_msg:
                    refs = old_msg['References'].split() + refs
        except OSError:
            pass

    account_idx = compose_model.account_for_message(msg)
    acct_name = compose_model.account_name(account_idx)
    quote_anchor = compose_model.normalize_body(seed.quoted_tail) if seed.quoted_tail else ''

    body = seed.body
    if getattr(settings, 'use_signature', True) and acct_name:
        sig_text, _ = signature.load(acct_name)
        if sig_text:
            sig_block = compose_model.sig_block_text(sig_text)
            if quote_anchor:
                body = '\n\n' + sig_block + '\n' + quote_anchor
            else:
                body = '\n\n' + sig_block

    return {
        'account': acct_name,
        'to': seed.to_text,
        'cc': seed.cc_text,
        'subject': seed.subject,
        'body': body,
        'quote_anchor': quote_anchor,
        'in_reply_to': in_reply_to,
        'references': ' '.join(refs),
    }


def get_configured_accounts() -> list[str]:
    """Return list of configured SMTP sender account identifiers."""
    accts: list[str] = []
    if isinstance(settings.smtp_accounts, dict):
        accts = list(settings.smtp_accounts.keys())
    elif isinstance(settings.smtp_accounts, (list, tuple)):
        accts = list(settings.smtp_accounts)
    elif isinstance(settings.email_address, dict):
        accts = list(settings.email_address.keys())

    if not accts:
        accts = ['default']
    return accts


def get_signatures() -> dict[str, Any]:
    """Return map of account name to plaintext signature and use_signature setting."""
    use_sig = getattr(settings, 'use_signature', True)
    sigs: dict[str, str] = {}
    for acct in get_configured_accounts():
        text, _ = signature.load(acct)
        sigs[acct] = text or ''
    return {
        'use_signature': use_sig,
        'signatures': sigs,
    }


def send_email(
    account: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_text: str,
    in_reply_to: str = '',
    references: str = '',
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> tuple[bool, str]:
    """Build MIME message, dispatch via msmtp, save sent copy, and run notmuch new."""
    acct = account or get_configured_accounts()[0]

    from_addr = ''
    if isinstance(settings.email_address, dict):
        from_addr = settings.email_address.get(acct, '')
    elif isinstance(settings.email_address, str):
        from_addr = settings.email_address

    temp_files: list[str] = []
    temp_dir: str | None = None
    if attachments:
        temp_dir = tempfile.mkdtemp(prefix='lazarus-web-send-')
        for filename, _, content_bytes in attachments:
            clean_name = mail_utils.sanitize_filename(filename or 'attachment')
            p = os.path.join(temp_dir, clean_name)
            with open(p, 'wb') as f:
                f.write(content_bytes)
            temp_files.append(p)

    data = mime_builder.ComposeData(
        from_addr=from_addr,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body_text=body_text,
        attachments=temp_files,
        in_reply_to=in_reply_to,
        references=references,
    )

    try:
        eml = mime_builder.build_message(data)

        if in_reply_to:
            eml['In-Reply-To'] = in_reply_to
        if references:
            eml['References'] = references

        # Resolve command
        if isinstance(settings.send_mail_command, dict):
            cmd_tmpl = settings.send_mail_command.get(acct, 'msmtp -a "{account}" -t')
        else:
            cmd_tmpl = settings.send_mail_command
        cmd_str = cmd_tmpl.format(account=acct)
        cmd_args = shlex.split(cmd_str)

        proc = subprocess.Popen(
            cmd_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        msg_bytes = eml.as_bytes()
        stdout, stderr = proc.communicate(input=msg_bytes, timeout=60)

        if proc.returncode != 0:
            err_msg = stderr.decode('utf-8', errors='replace').strip() or f"Exit code {proc.returncode}"
            logger.warning('Failed to send mail via %r: %s', cmd_str, err_msg)
            return (False, f"Send command failed: {err_msg}")

        # Save to sent_dir if configured
        sent_dest: str | None = None
        if isinstance(settings.sent_dir, dict):
            sent_dest = settings.sent_dir.get(acct)
        elif isinstance(settings.sent_dir, str):
            sent_dest = settings.sent_dir

        if sent_dest:
            expanded_sent = os.path.expanduser(sent_dest)
            try:
                os.makedirs(expanded_sent, exist_ok=True)
                box = mailbox.Maildir(expanded_sent, create=True)
                box.add(eml)
            except Exception as e:
                logger.warning('Could not save sent message to %r: %s', sent_dest, e)

        # Index sent copy
        try:
            notmuch.new(no_hooks=settings.no_hooks_on_send)
        except Exception as e:
            logger.warning('notmuch new failed after send: %s', e)

        return (True, 'Message sent successfully')
    except Exception as e:
        logger.exception('Exception while building or sending email: %s', e)
        return (False, str(e))
    finally:
        if temp_dir and os.path.isdir(temp_dir):
            for p in temp_files:
                try:
                    os.remove(p)
                except OSError:
                    pass
            try:
                os.rmdir(temp_dir)
            except OSError:
                pass
