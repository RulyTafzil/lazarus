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
# Dodo is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Lazarus. If not, see <https://www.gnu.org/licenses/>.

"""Shared email actions used by search, dashboard, and thread panels.

This module centralises delete, archive, and file-move operations that
were previously duplicated across SearchPanel, DashboardPanel, and
ThreadPanel.

File moves run on a background ``QThread`` so rapid successive bulk
actions queue up instead of interrupting each other.  Tagging and
notmuch queries stay synchronous so the UI reflects changes instantly.
"""

from __future__ import annotations
import os
import re
import subprocess
import logging
import queue
from typing import Set, Optional, List, Tuple

from PyQt6.QtCore import QThread, pyqtSignal

from . import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Background worker for file moves
# ---------------------------------------------------------------------------

class _BulkMoveWorker(QThread):
    """Serialises file moves and runs ``notmuch new`` after each batch."""

    batch_done = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.queue: queue.Queue[Tuple[str, str] | None] = queue.Queue()
        self._batches_pending = 0

    def enqueue(self, moves: List[Tuple[str, str]]) -> None:
        """Push a batch of (src, dst) moves, followed by a sentinel."""
        self._batches_pending += 1
        for src, dst in moves:
            self.queue.put((src, dst))
        self.queue.put(None)  # sentinel marking end of batch

    def run(self) -> None:
        while True:
            try:
                item = self.queue.get(timeout=30)
            except queue.Empty:
                return
            if item is None:
                self._batches_pending -= 1
                self.batch_done.emit()
                continue
            src, dst = item
            if not os.path.exists(src):
                logger.debug('skip (already moved): %s', src)
                continue
            try:
                os.rename(src, dst)
            except OSError as e:
                logger.warning('move failed: %s → %s: %s', src, dst, e)


# Singleton worker, started on first use
_worker: _BulkMoveWorker | None = None


def _get_worker() -> _BulkMoveWorker:
    global _worker
    if _worker is None or not _worker.isRunning():
        _worker = _BulkMoveWorker()
        _worker.start()
    return _worker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_archive_refused(tags: Set[str]) -> bool:
    """Return True if archiving should be refused.

    A thread must have at least one categorising tag beyond inbox/unread
    to be eligible for archiving.
    """
    return len(tags - {'inbox', 'unread'}) == 0


def _mail_file_account(filepath: str) -> Optional[tuple[str, str]]:
    """Split a mail file path into (account, rest_of_path).

    Returns None if the path doesn't live under the configured mail root.
    """
    mail_root = os.path.expanduser(settings.mail_root)
    if filepath.startswith(mail_root + '/'):
        rel = filepath[len(mail_root) + 1:]
    elif '/Mail/' in filepath:
        _, rel = filepath.split('/Mail/', 1)
    else:
        return None
    parts = rel.split('/', 1)
    if len(parts) != 2:
        return None
    return (parts[0], parts[1])


def _find_trash_dir(account: str) -> str:
    """Return the Trash cur/ directory for *account*, creating it if needed."""
    mail_root = os.path.expanduser(settings.mail_root)
    trash_dir = os.path.join(mail_root, account, '[Gmail]', 'Trash', 'cur')
    if not os.path.isdir(trash_dir):
        trash_dir = os.path.join(mail_root, account, 'Trash', 'cur')
    os.makedirs(trash_dir, exist_ok=True)
    return trash_dir


def _find_archive_dir() -> str:
    """Return the local Archive cur/ directory, creating it if needed."""
    archive_cur = os.path.join(
        os.path.expanduser(settings.archive_dir), 'cur')
    os.makedirs(archive_cur, exist_ok=True)
    return archive_cur


def _strip_uid_annotation(filename: str) -> str:
    """Remove mbsync UID annotations to avoid duplicate-UID errors."""
    return re.sub(r',U=\d+', '', filename)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def move_to_trash(notmuch_query: str) -> int:
    """Tag ``+deleted`` and move matching files to the Trash folder.

    Tagging is synchronous (instant UI feedback).  File moves are
    enqueued to a background thread so rapid successive calls don't
    interrupt each other.

    Returns the number of *files found* (moves happen asynchronously).
    """
    # Search BEFORE tagging — tag changes may alter query matching.
    r = subprocess.run(
        ['notmuch', 'search', '--exclude=false', '--output=files',
         '--', notmuch_query],
        capture_output=True, text=True)

    subprocess.run(
        ['notmuch', 'tag', '+deleted', '-inbox', '-unread',
         '-marked', '--', notmuch_query])

    moves: List[Tuple[str, str]] = []
    found = 0
    seen = set()
    for f in r.stdout.strip().split('\n'):
        if not f or f in seen:
            continue
        seen.add(f)
        found += 1
        result = _mail_file_account(f)
        if result is None:
            continue
        account, _ = result
        trash_dir = _find_trash_dir(account)
        basename = _strip_uid_annotation(os.path.basename(f))
        moves.append((f, os.path.join(trash_dir, basename)))

    if moves:
        _get_worker().enqueue(moves)
        subprocess.run(['notmuch', 'new', '--no-hooks'],
                       capture_output=True)
    return found


def move_to_archive(notmuch_query: str) -> int:
    """Tag ``-inbox -unread`` and move matching files to local Archive.

    Tagging is synchronous.  File moves are enqueued to a background
    thread.

    Returns the number of *files found*.
    """
    # Search BEFORE tagging — tag removal may alter query matching.
    r = subprocess.run(
        ['notmuch', 'search', '--exclude=false', '--output=files',
         '--', notmuch_query],
        capture_output=True, text=True)

    subprocess.run(
        ['notmuch', 'tag', '-inbox', '-unread', '-marked', '--',
         notmuch_query])

    archive_cur = _find_archive_dir()

    moves: List[Tuple[str, str]] = []
    found = 0
    seen = set()
    for f in r.stdout.strip().split('\n'):
        if not f or f in seen:
            continue
        seen.add(f)
        found += 1
        basename = _strip_uid_annotation(os.path.basename(f))
        moves.append((f, os.path.join(archive_cur, basename)))

    if moves:
        _get_worker().enqueue(moves)
        subprocess.run(['notmuch', 'new', '--no-hooks'],
                       capture_output=True)
    return found
