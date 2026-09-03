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
"""Core mail synchronization engine.

Runs parallel mbsync across configured accounts, indexes new messages with
notmuch new, and applies automated mail filter rules. Completely headless
and pure Python stdlib.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import logging
import os
import re
import select
import shlex
import signal
import subprocess
from typing import Callable, List, Optional, Tuple

from .. import notmuch
from .. import rules
from .. import settings

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Outcome of a mail synchronization cycle."""
    ok: bool
    sync_rc: int = 0
    notmuch_rc: int = 0
    sync_stderr: str = ''
    notmuch_stderr: str = ''
    sync_summaries: list[str] = field(default_factory=list)
    new_count: int = 0
    flagged_count: int = 0
    cleaned_count: int = 0
    deleted_count: int = 0
    message: str = ''


def parse_sync_stats(summaries: list[str]) -> Tuple[int, int, int, int]:
    """Extract (new, flagged, expunged, deleted) counts from mbsync Far lines."""
    new = flagged = expunged = deleted = 0
    for summary in summaries:
        m = re.search(r'Far:\s*\+(\d+)\s*\*(\d+)\s*#(\d+)\s*-(\d+)', summary)
        if m:
            new += int(m.group(1))
            flagged += int(m.group(2))
            expunged += int(m.group(3))
            deleted += int(m.group(4))
    return new, flagged, expunged, deleted


def format_sync_summary(new: int, flagged: int, expunged: int, deleted: int) -> str:
    """Format human-readable sync summary string."""
    bits = []
    if new != 0: bits.append(f'+{new} new')
    if flagged != 0: bits.append(f'*{flagged} flagged')
    if expunged != 0: bits.append(f'{expunged} cleaned')
    if deleted != 0: bits.append(f'{deleted} deleted')
    if bits:
        return f"Sync completed ({', '.join(bits)})"
    return "Sync completed (no new mail)"


def _kill_subprocesses(procs: list[subprocess.Popen[str]]) -> None:
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except OSError:
            pass


def run_sync(
    progress_callback: Optional[Callable[[str], None]] = None,
    accounts: Optional[List[str]] = None,
    sync_cmd: Optional[str] = None,
    apply_rules: bool = True,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> SyncResult:
    """Execute mail synchronization.

    1. Runs mbsync in parallel per account (or sync_cmd fallback).
    2. Runs notmuch new.
    3. Runs filter rules if configured.
    """
    def emit(text: str) -> None:
        if progress_callback:
            try:
                progress_callback(text)
            except Exception as e:
                logger.debug('progress_callback exception: %s', e)

    def is_cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    target_accounts = accounts if accounts is not None else settings.smtp_accounts
    active_procs: list[subprocess.Popen[str]] = []
    sync_rc = 0
    sync_summaries: list[str] = []
    sync_stderr = ''

    # 1. mbsync phase
    if target_accounts:
        out_map: dict[int, tuple[subprocess.Popen[str], str]] = {}
        err_map: dict[int, tuple[subprocess.Popen[str], str]] = {}
        err_buffers: dict[str, list[str]] = {acct: [] for acct in target_accounts}

        for acct in target_accounts:
            emit(f'Syncing: {acct}...')
            try:
                p = subprocess.Popen(
                    ['mbsync', '-V', acct],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                    universal_newlines=True,
                )
                assert p.stdout is not None and p.stderr is not None
                out_map[p.stdout.fileno()] = (p, acct)
                err_map[p.stderr.fileno()] = (p, acct)
                active_procs.append(p)
            except Exception as e:
                logger.warning('Failed starting mbsync for %s: %s', acct, e)
                err_buffers[acct].append(str(e))
                sync_rc = 1

        while out_map or err_map:
            if is_cancelled():
                _kill_subprocesses(active_procs)
                return SyncResult(ok=False, sync_rc=130, message='Sync cancelled')

            try:
                readable, _, _ = select.select(list(out_map) + list(err_map), [], [], 0.5)
            except (ValueError, OSError):
                break

            for fd in readable:
                if fd in err_map:
                    proc, acct = err_map[fd]
                    assert proc.stderr is not None
                    line = proc.stderr.readline()
                    if line:
                        err_buffers[acct].append(line.strip())
                    else:
                        del err_map[fd]
                elif fd in out_map:
                    proc, acct = out_map[fd]
                    assert proc.stdout is not None
                    line = proc.stdout.readline()
                    if not line:
                        del out_map[fd]
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith('Opening far side box '):
                        box = line[21:].rstrip('...')
                        emit(f'  {acct}: {box}')
                    elif line.startswith('Channels:'):
                        sync_summaries.append(f'{acct}: {line}')

        for p in active_procs:
            p.wait()
            if p.returncode != 0 and sync_rc == 0:
                sync_rc = p.returncode

        combined_err: list[str] = []
        for acct in target_accounts:
            lines = [l for l in err_buffers.get(acct, []) if l]
            if lines:
                combined_err.append(f'{acct}: {" ".join(lines)}')
        sync_stderr = '\n'.join(combined_err)

    elif sync_cmd or settings.sync_mail_command:
        cmd = sync_cmd or settings.sync_mail_command
        emit('Syncing (all)...')
        try:
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
                start_new_session=True,
                universal_newlines=True,
            )
            active_procs = [p]
            assert p.stdout is not None and p.stderr is not None
            out_fd = p.stdout.fileno()
            err_fd = p.stderr.fileno()
            active_fds = {out_fd, err_fd}
            err_lines: list[str] = []

            while active_fds:
                if is_cancelled():
                    _kill_subprocesses(active_procs)
                    return SyncResult(ok=False, sync_rc=130, message='Sync cancelled')
                try:
                    readable, _, _ = select.select(list(active_fds), [], [], 0.5)
                except (ValueError, OSError):
                    break
                for fd in readable:
                    if fd == err_fd:
                        line = p.stderr.readline()
                        if line:
                            err_lines.append(line.strip())
                        else:
                            active_fds.discard(err_fd)
                    elif fd == out_fd:
                        line = p.stdout.readline()
                        if not line:
                            active_fds.discard(out_fd)
                            continue
                        line = line.strip()
                        if line:
                            emit(line)

            p.wait()
            sync_rc = p.returncode
            sync_stderr = '\n'.join(err_lines)
        except Exception as e:
            logger.warning('Failed running sync command %r: %s', cmd, e)
            sync_rc = 1
            sync_stderr = str(e)

    if is_cancelled():
        return SyncResult(ok=False, sync_rc=130, message='Sync cancelled')

    # 2. notmuch new phase
    emit('Indexing...')
    notmuch_rc = 0
    notmuch_stderr = ''
    try:
        try:
            notmuch.new(no_hooks=False)
        except TypeError:
            notmuch.new()  # type: ignore[call-arg]
    except Exception as e:
        logger.warning('notmuch new failed: %s', e)
        notmuch_rc = 1
        notmuch_stderr = str(e)

    # 3. filter rules phase
    if notmuch_rc == 0 and apply_rules and settings.filter_rules:
        try:
            rules.apply_rules(settings.filter_rules, settings.filter_scope_query)
        except Exception as e:
            logger.warning('Error applying filter rules: %s', e)

    # 4. format outcome
    new, flagged, expunged, deleted = parse_sync_stats(sync_summaries)
    ok = (sync_rc == 0 and notmuch_rc == 0)

    if sync_rc != 0:
        summary_msg = f'Sync error (exit {sync_rc})'
        if sync_stderr:
            summary_msg += f': {sync_stderr[:200]}'
    elif notmuch_rc != 0:
        summary_msg = f'notmuch error (exit {notmuch_rc})'
        if notmuch_stderr:
            summary_msg += f': {notmuch_stderr[:200]}'
    else:
        summary_msg = format_sync_summary(new, flagged, expunged, deleted)

    return SyncResult(
        ok=ok,
        sync_rc=sync_rc,
        notmuch_rc=notmuch_rc,
        sync_stderr=sync_stderr,
        notmuch_stderr=notmuch_stderr,
        sync_summaries=sync_summaries,
        new_count=new,
        flagged_count=flagged,
        cleaned_count=expunged,
        deleted_count=deleted,
        message=summary_msg,
    )
