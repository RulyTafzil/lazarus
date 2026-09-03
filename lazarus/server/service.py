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
from typing import Any

from .. import actions
from .. import compose_model
from .. import mail_utils
from .. import mime_builder
from .. import notmuch
from .. import settings

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


def _flatten_messages(node: Any, output: list[dict[str, Any]]) -> None:
    """Recursively flatten notmuch show JSON tree into message list."""
    if isinstance(node, list):
        for item in node:
            _flatten_messages(item, output)
    elif isinstance(node, dict) and 'id' in node:
        output.append(node)


def get_thread_messages(thread_id: str) -> dict[str, Any]:
    """Fetch all messages in a thread and extract clean display details."""
    clean_id = thread_id.removeprefix('thread:')
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
        return {'thread_id': clean_id, 'subject': '', 'tags': [], 'messages': []}

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
    }


def get_part_data(message_id: str, part_id: int) -> tuple[bytes, str, str]:
    """Retrieve raw bytes for an attachment part."""
    try:
        content = notmuch.show_part(part_id, message_id, decrypt=True)
    except Exception as e:
        logger.warning('Failed to fetch part %d for message %r: %s', part_id, message_id, e)
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
            f'id:{message_id}',
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
        clean = item.strip()
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


def archive_thread(thread_id: str) -> bool:
    """A archive: tag -inbox -unread, move files to local Archive Maildir, run notmuch new."""
    clean_id = thread_id.removeprefix('thread:')
    query = f"thread:{clean_id}"
    files = actions.collect_files(query)
    notmuch.tag('-inbox -unread', query)
    if not files:
        return True

    archive_dir = os.path.expanduser(settings.archive_dir)
    os.makedirs(os.path.join(archive_dir, 'cur'), exist_ok=True)

    resolved: list[str] = []
    for f in files:
        r = actions._resolve_stale_path(f)
        if r:
            resolved.append(r)

    moves = actions.plan_archive_moves(resolved, archive_dir)
    for src, dst in moves:
        if os.path.exists(src):
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.rename(src, dst)
            except OSError as e:
                logger.warning('Archive move failed: %s -> %s: %s', src, dst, e)

    try:
        notmuch.new(no_hooks=True)
    except Exception as e:
        logger.warning('notmuch new failed after archive move: %s', e)

    return True


def unarchive_thread(thread_id: str) -> bool:
    """Restore thread to inbox."""
    clean_id = thread_id.removeprefix('thread:')
    return modify_tags([f"thread:{clean_id}"], add_tags=['inbox'], remove_tags=[])


def trash_thread(thread_id: str) -> bool:
    """Trash thread: tag +trash -inbox -unread, move files to account Trash, run notmuch new."""
    clean_id = thread_id.removeprefix('thread:')
    query = f"thread:{clean_id}"
    files = actions.collect_files(query)
    notmuch.tag('+trash -inbox -unread', query)
    if not files:
        return True

    mail_root = os.path.expanduser(settings.mail_root)
    resolved: list[str] = []
    for f in files:
        r = actions._resolve_stale_path(f)
        if r:
            resolved.append(r)

    moves = actions.plan_trash_moves(resolved, mail_root)
    for src, dst in moves:
        if os.path.exists(src):
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.rename(src, dst)
            except OSError as e:
                logger.warning('Trash move failed: %s -> %s: %s', src, dst, e)

    try:
        notmuch.new(no_hooks=True)
    except Exception as e:
        logger.warning('notmuch new failed after trash move: %s', e)

    return True


def untrash_thread(thread_id: str) -> bool:
    """Restore thread from trash to inbox."""
    clean_id = thread_id.removeprefix('thread:')
    return modify_tags([f"thread:{clean_id}"], add_tags=['inbox'], remove_tags=['trash'])


def sync_mail() -> tuple[bool, str]:
    """Execute parallel mbsync per account (or sync_mail_command), run notmuch new, apply filter rules."""
    accounts = settings.smtp_accounts if settings.smtp_accounts else []
    procs: list[tuple[str, subprocess.Popen[bytes]]] = []

    if accounts:
        for acct in accounts:
            try:
                p = subprocess.Popen(
                    ['mbsync', '-V', acct],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                procs.append((acct, p))
            except Exception as e:
                logger.warning('Failed launching mbsync for %s: %s', acct, e)

        for acct, p in procs:
            try:
                p.wait(timeout=120)
            except subprocess.TimeoutExpired:
                p.kill()
                logger.warning('mbsync timed out for %s', acct)
    elif settings.sync_mail_command:
        try:
            subprocess.run(shlex.split(settings.sync_mail_command), timeout=120)
        except Exception as e:
            logger.warning('Failed running sync_mail_command: %s', e)

    # notmuch new
    try:
        notmuch.new()
    except Exception as e:
        logger.warning('notmuch new failed during sync: %s', e)

    # filter rules
    if settings.filter_rules:
        try:
            from .. import rules
            rules.apply_rules(settings.filter_rules, settings.filter_scope_query)
        except Exception as e:
            logger.warning('rules.apply_rules failed: %s', e)

    return (True, 'Sync completed successfully')


def toggle_flag(thread_id: str, flag: bool) -> bool:
    """Add or remove flagged tag from thread."""
    clean_id = thread_id.removeprefix('thread:')
    if flag:
        return modify_tags([f"thread:{clean_id}"], add_tags=['flagged'], remove_tags=[])
    return modify_tags([f"thread:{clean_id}"], add_tags=[], remove_tags=['flagged'])


def get_all_tags() -> list[dict[str, Any]]:
    """Return all known tags with thread counts."""
    all_tags = sorted(notmuch.tags())
    queries = [f'tag:{t}' for t in all_tags]
    counts = notmuch.count_batch(queries, output='threads')
    return [{'name': t, 'count': c} for t, c in zip(all_tags, counts)]


# ---------------------------------------------------------------------------
# Reply Seeds and Outbound Delivery
# ---------------------------------------------------------------------------

def get_reply_seed(message_id: str, to_all: bool = False) -> dict[str, Any]:
    """Build pre-populated reply data for a given message ID."""
    clean_id = message_id.removeprefix('id:').strip('<>')
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

    return {
        'to': seed.to_text,
        'cc': seed.cc_text,
        'subject': seed.subject,
        'body': seed.body,
        'in_reply_to': in_reply_to,
        'references': ' '.join(refs),
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
    acct = account or (settings.smtp_accounts[0] if settings.smtp_accounts else 'default')

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
