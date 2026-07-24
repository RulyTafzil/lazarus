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
"""

from __future__ import annotations
import os
import re
import subprocess
import logging
import threading
from typing import Set, Optional

from . import settings

logger = logging.getLogger(__name__)

# Prevent concurrent bulk move operations (they'd step on each other's
# notmuch database state).
_move_lock = threading.Lock()


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
        # Fallback for hardcoded paths that predate the mail_root setting.
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


def move_to_trash(notmuch_query: str) -> int:
    """Tag ``+deleted`` and move matching files to the Trash folder.

    Returns the number of files successfully moved.
    """
    _move_lock.acquire()
    try:
        # Search BEFORE tagging — tag changes may alter query matching.
        r = subprocess.run(
            ['notmuch', 'search', '--exclude=false', '--output=files',
             '--', notmuch_query],
            capture_output=True, text=True)

        subprocess.run(
            ['notmuch', 'tag', '+deleted', '-inbox', '-unread',
             '-marked', '--', notmuch_query])

        moved = 0
        seen = set()
        for f in r.stdout.strip().split('\n'):
            if not f or f in seen:
                continue
            seen.add(f)
            result = _mail_file_account(f)
            if result is None:
                continue
            account, _ = result
            trash_dir = _find_trash_dir(account)
            basename = _strip_uid_annotation(os.path.basename(f))
            dest = os.path.join(trash_dir, basename)
            if not os.path.exists(f):
                logger.debug('trash skip (already moved): %s', f)
                continue
            try:
                os.rename(f, dest)
                moved += 1
            except OSError as e:
                logger.warning('trash move failed: %s', e)
        if moved:
            subprocess.run(['notmuch', 'new', '--no-hooks'],
                           capture_output=True)
        return moved
    finally:
        _move_lock.release()


def move_to_archive(notmuch_query: str) -> int:
    """Tag ``-inbox -unread`` and move matching files to the local Archive.

    Returns the number of files successfully moved.
    """
    _move_lock.acquire()
    try:
        # Search BEFORE tagging — tag removal may alter query matching.
        r = subprocess.run(
            ['notmuch', 'search', '--exclude=false', '--output=files',
             '--', notmuch_query],
            capture_output=True, text=True)

        subprocess.run(
            ['notmuch', 'tag', '-inbox', '-unread', '-marked', '--',
             notmuch_query])

        archive_cur = _find_archive_dir()

        moved = 0
        seen = set()
        for f in r.stdout.strip().split('\n'):
            if not f or f in seen:
                continue
            seen.add(f)
            basename = _strip_uid_annotation(os.path.basename(f))
            dest = os.path.join(archive_cur, basename)
            if not os.path.exists(f):
                logger.debug('archive skip (already moved): %s', f)
                continue
            try:
                os.rename(f, dest)
                moved += 1
            except OSError as e:
                logger.warning('archive move failed: %s', e)
        if moved:
            subprocess.run(['notmuch', 'new', '--no-hooks'],
                           capture_output=True)
        return moved
    finally:
        _move_lock.release()
