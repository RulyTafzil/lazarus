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
"""Core email domain actions and background file-move worker.

This module is completely headless and has ZERO Qt dependencies.
File moves run on a background standard Python ``threading.Thread`` so rapid
successive bulk actions queue up safely instead of interrupting each other.
``notmuch new`` is fired only after a batch of moves has landed on disk.
"""

from __future__ import annotations
import logging
import os
import queue
import re
import sys
import threading
import time
from typing import Callable, List, Optional, Set, Tuple

from . import notmuch
from . import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Background worker for file moves
# ---------------------------------------------------------------------------

_SHUTDOWN = object()


class _BulkMoveWorker(threading.Thread):
    """Serialises file moves and runs ``notmuch new`` after each batch."""

    def __init__(self) -> None:
        super().__init__(name="BulkMoveWorker", daemon=True)
        self.queue: queue.Queue[Tuple[str, str] | object | None] = queue.Queue()
        self._lock = threading.Lock()
        self._batches_pending = 0
        self._shutting_down = False
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, fn: Callable[[], None]) -> None:
        """Register a callback to be called when a batch finishes."""
        with self._lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[], None]) -> None:
        """Unregister a batch done callback."""
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def isRunning(self) -> bool:
        """Backward compatibility with QThread.isRunning()."""
        return self.is_alive()

    def isFinished(self) -> bool:
        """Backward compatibility with QThread.isFinished()."""
        return not self.is_alive()

    def enqueue(self, moves: List[Tuple[str, str]]) -> None:
        """Push a batch of (src, dst) moves, followed by a sentinel."""
        if self._shutting_down:
            return
        with self._lock:
            self._batches_pending += 1
        for src, dst in moves:
            self.queue.put((src, dst))
        self.queue.put(None)

    def shutdown(self, timeout_ms: int = 5000) -> None:
        """Request exit — enqueues the shutdown sentinel, wakes the thread."""
        self._shutting_down = True
        self.queue.put(_SHUTDOWN)
        self.join(timeout=timeout_ms / 1000.0)

    def is_idle(self) -> bool:
        with self._lock:
            return self._batches_pending == 0

    def wait_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._batches_pending == 0:
                    return True
            time.sleep(0.01)
        return False

    def _try_move(self, src: str, dst: str) -> None:
        """Move ``src`` → ``dst``, tolerating renames that land after planning.

        The queue can hold a batch for a moment after ``move_to_trash`` /
        ``move_to_archive`` plan it, and in that window the source path can
        go stale: notmuch (``maildir.synchronize_flags``) renames files when
        tags change the Maildir flags, and a concurrent mbsync rewrites
        ``,U=`` annotations / flags. Renaming a stale path silently loses
        the move — the message stays in its folder with the tag applied.

        So on a missing source we follow the file by its stem (same dir +
        cur/new sibling, see ``_resolve_stale_path``) and re-derive the
        destination basename from the resolved name so the final Maildir
        flags match.
        """
        if not os.path.exists(src):
            resolved = _resolve_stale_path(src)
            if resolved is None or resolved == src:
                logger.debug('skip (already moved): %s', os.path.basename(src))
                return
            src = resolved
            dst = _unique_dest(os.path.join(
                os.path.dirname(dst),
                _strip_uid_annotation(os.path.basename(resolved))))
        try:
            os.rename(src, dst)
        except OSError as e:
            # Second chance: renamed between the existence check and the
            # syscall (narrow race with flag-sync / mbsync).
            resolved = _resolve_stale_path(src)
            if resolved is not None and resolved != src and os.path.exists(resolved):
                try:
                    os.rename(resolved, _unique_dest(os.path.join(
                        os.path.dirname(dst),
                        _strip_uid_annotation(os.path.basename(resolved)))))
                    return
                except OSError as e2:
                    logger.warning('move failed: %s → %s: %s', resolved, dst, e2)
                    return
            logger.warning('move failed: %s → %s: %s', src, dst, e)

    def run(self) -> None:
        while True:
            item = self.queue.get()
            if item is _SHUTDOWN:
                return
            if item is None:
                with self._lock:
                    self._batches_pending -= 1
                try:
                    notmuch.new(no_hooks=True)
                except Exception as e:
                    logger.warning('notmuch new failed: %s', e)

                with self._lock:
                    callbacks = list(self._listeners)
                for cb in callbacks:
                    try:
                        cb()
                    except Exception as e:
                        logger.warning('batch_done listener failed: %s', e)
                continue
            if not isinstance(item, tuple):
                continue
            src, dst = item
            self._try_move(src, dst)


# Singleton worker, started on first use and kept alive for the session.
_worker: _BulkMoveWorker | None = None
_batch_done_listener: Optional[Callable[[], None]] = None


def set_batch_done_listener(fn: Optional[Callable[[], None]]) -> None:
    """Register (or clear, with None) the app-level slot run after each
    completed move batch.
    """
    global _batch_done_listener
    if _worker is not None and _batch_done_listener is not None:
        _worker.remove_listener(_batch_done_listener)
    _batch_done_listener = fn
    if fn is not None and _worker is not None and _worker.is_alive():
        _worker.add_listener(fn)


def _get_worker() -> _BulkMoveWorker:
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = _BulkMoveWorker()
        if _batch_done_listener is not None:
            _worker.add_listener(_batch_done_listener)
        _worker.start()
    return _worker


def get_worker() -> _BulkMoveWorker:
    """Public accessor for the singleton background move worker."""
    return _get_worker()


def shutdown_worker() -> None:
    """Call on application shutdown — joins the worker if alive."""
    if _worker is not None and _worker.is_alive():
        try:
            _worker.shutdown()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_archive_refused(tags: Set[str]) -> bool:
    """Return True if archiving should be refused.

    A thread must have at least one categorising tag beyond inbox/unread
    to be eligible for archiving.
    """
    return len(tags - {'inbox', 'unread'}) == 0


def _mail_file_account(
        filepath: str,
        mail_root: Optional[str] = None) -> Optional[tuple[str, str]]:
    """Split a mail file path into (account, rest_of_path)."""
    mail_root = os.path.expanduser(mail_root or settings.mail_root)
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


def _trash_dir_path(account: str, mail_root: str) -> str:
    """Trash cur/ directory for *account* (pure path; not created)."""
    gmail = os.path.join(mail_root, account, '[Gmail]', 'Trash', 'cur')
    if os.path.isdir(gmail):
        return gmail
    return os.path.join(mail_root, account, 'Trash', 'cur')


def _find_trash_dir(account: str) -> str:
    """Return the Trash cur/ directory for *account*, creating it if needed."""
    trash_dir = _trash_dir_path(account, os.path.expanduser(settings.mail_root))
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


def _unique_dest(path: str) -> str:
    """Return *path* unchanged if it does not exist, otherwise append a counter."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    counter = 1
    while True:
        candidate = f'{base}.{counter}{ext}'
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def _resolve_stale_path(f: str) -> Optional[str]:
    """Resolve a file path that may have changed flags or directory.

    Message identity is the maildir *stem* (the name before ``:2,``):
    read/flag renames (``:2,S``… a notmuch ``maildir.synchronize_flags``
    side effect) and ``new/``↔``cur/`` moves preserve it.  We compare
    stems for *exact equality* first, and fall back to comparing with
    mbsync UID annotations stripped.
    """
    if os.path.exists(f):
        return f
    parent = os.path.dirname(f)
    stem_base = _stem_of(os.path.basename(f))

    candidates = [parent]
    if os.path.basename(parent) in ('new', 'cur'):
        maildir_base = os.path.dirname(parent)
        for sub in ('cur', 'new'):
            sibling = os.path.join(maildir_base, sub)
            if sibling not in candidates:
                candidates.append(sibling)

    clean_stem_base = _strip_uid_annotation(stem_base)
    for directory in candidates:
        try:
            entries = os.listdir(directory)
            for entry in entries:
                if _stem_of(entry) == stem_base:
                    return os.path.join(directory, entry)
            for entry in entries:
                if _strip_uid_annotation(_stem_of(entry)) == clean_stem_base:
                    return os.path.join(directory, entry)
        except OSError:
            pass
    return None


def _stem_of(filename: str) -> str:
    """Maildir stem: the unique name without the ``:2,`` flag suffix."""
    return filename.rsplit(':2,', 1)[0] if ':2,' in filename else filename


# ---------------------------------------------------------------------------
# Public Domain Move Actions
# ---------------------------------------------------------------------------

def collect_files(query: str) -> list[str]:
    """Return deduplicated, resolved file paths matching *query*."""
    files: list[str] = []
    seen: set[str] = set()
    for f in notmuch.search_files(query, exclude_false=True):
        if f in seen:
            continue
        seen.add(f)
        resolved = _resolve_stale_path(f)
        if resolved is None:
            logger.debug('file gone: %s', os.path.basename(f))
            continue
        files.append(resolved)
    return files


def _is_trash_path(path: str) -> bool:
    """Return True if *path* lives inside a Trash Maildir folder."""
    return '/Trash/' in path or '/[Gmail]/Trash/' in path


def plan_trash_moves(files: List[str],
                     mail_root: str) -> List[Tuple[str, str]]:
    """Compute ``(src, dst)`` moves to each file's account Trash folder."""
    mail_root = os.path.expanduser(mail_root)
    moves: List[Tuple[str, str]] = []
    for f in files:
        result = _mail_file_account(f, mail_root)
        if result is None:
            continue
        account, _ = result
        trash_dir = _trash_dir_path(account, mail_root)
        if f.startswith(trash_dir + os.sep):
            logger.debug('skip (already in trash): %s', os.path.basename(f))
            continue
        basename = _strip_uid_annotation(os.path.basename(f))
        moves.append((f, _unique_dest(os.path.join(trash_dir, basename))))
    return moves


def plan_archive_moves(files: List[str],
                       archive_dir: str) -> List[Tuple[str, str]]:
    """Compute ``(src, dst)`` moves into ``archive_dir/cur``."""
    archive_cur = os.path.join(os.path.expanduser(archive_dir), 'cur')
    moves: List[Tuple[str, str]] = []
    for f in files:
        if f.startswith(archive_cur + os.sep):
            logger.debug('skip (already in target): %s', os.path.basename(f))
            continue
        basename = _strip_uid_annotation(os.path.basename(f))
        moves.append((f, _unique_dest(os.path.join(archive_cur, basename))))
    return moves


def _get_collector() -> Callable[[str], list[str]]:
    """Return the active file collector for a move operation.

    Core move actions resolve the collector through ``lazarus.actions``
    when it exposes a different ``collect_files`` than this module's own.
    That is the swappable seam the desktop/tests use to override file
    collection (e.g. ``monkeypatch.setattr(lazarus.actions, 'collect_files', fn)``
    in tests, or a future UI-level collector) without changing core
    semantics. In the headless daemon ``lazarus.actions`` is never
    imported, so this resolves to the core collector.
    """
    actions_mod = sys.modules.get('lazarus.actions')
    if actions_mod and hasattr(actions_mod, 'collect_files'):
        fn = getattr(actions_mod, 'collect_files')
        if fn is not collect_files:
            return fn  # type: ignore[no-any-return]
    return collect_files


def move_to_trash(notmuch_query: str, unmark: bool = True, exclude_marked: bool | None = None) -> int:
    """Tag ``+trash -inbox -unread`` and move matching files to account Trash."""
    files = _get_collector()(notmuch_query)
    if not files:
        return 0

    should_unmark = unmark if exclude_marked is None else exclude_marked
    notmuch.tag('+trash -inbox -unread', notmuch_query, exclude_marked=should_unmark)

    resolved: List[str] = []
    for f in files:
        r = _resolve_stale_path(f)
        if r is None:
            logger.debug('file gone after tagging: %s', os.path.basename(f))
            continue
        resolved.append(r)

    moves = plan_trash_moves(resolved, os.path.expanduser(settings.mail_root))
    for _, dst in moves:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
    if moves:
        _get_worker().enqueue(moves)
    return len(moves)


def move_to_archive(notmuch_query: str, unmark: bool = True, exclude_marked: bool | None = None) -> int:
    """Untag ``-inbox -unread`` and move matching files to local ``archive_dir/cur/``."""
    files = _get_collector()(notmuch_query)
    if not files:
        return 0
    should_unmark = unmark if exclude_marked is None else exclude_marked
    notmuch.tag('-inbox -unread', notmuch_query, exclude_marked=should_unmark)
    return move_specific_files(files, os.path.expanduser(settings.archive_dir))


def move_specific_files(files: List[str], target_dir: str) -> int:
    """Move an already-resolved list of file paths into ``target_dir/cur/``."""
    target_dir = os.path.expanduser(target_dir)
    target_cur = os.path.join(target_dir, 'cur')
    os.makedirs(target_cur, exist_ok=True)

    resolved: List[str] = []
    for f in files:
        r = _resolve_stale_path(f)
        if r is None:
            logger.debug('file gone after tagging: %s', os.path.basename(f))
            continue
        resolved.append(r)

    moves = plan_archive_moves(resolved, target_dir)
    if moves:
        _get_worker().enqueue(moves)
    return len(moves)


def expunge_trash(trash_folder: Optional[str] = None) -> int:
    """Add the Maildir ``T`` (Trashed) flag to every file matching ``tag:trash``."""
    files = collect_files('tag:trash')

    tagged = 0
    for f in files:
        if not _is_trash_path(f):
            logger.debug('expunge: not in trash folder: %s', f)
            continue

        # Re-resolve right before the rename: the collect above may be a
        # beat old, and mbsync / flag-sync can rename the file meanwhile.
        resolved = _resolve_stale_path(f)
        if resolved is None:
            logger.debug('expunge: file gone: %s', os.path.basename(f))
            continue
        f = resolved

        dirname = os.path.dirname(f)
        basename = os.path.basename(f)

        if ':2,' in basename:
            base, flags = basename.split(':2,', 1)
            if 'T' in flags:
                continue
            new_flags = ''.join(sorted(set(flags + 'T')))
            new_name = f'{base}:2,{new_flags}'
        else:
            new_name = f'{basename}:2,T'

        new_path = os.path.join(dirname, new_name)
        try:
            os.rename(f, new_path)
            tagged += 1
        except OSError as e:
            logger.warning('expunge rename failed %s → %s: %s', f, new_path, e)

    if tagged:
        notmuch.tag('-trash', 'tag:trash')

    return tagged


def restore_from_trash(notmuch_query: str, unmark: bool = False) -> int:
    """Move files matching notmuch_query from Trash back to account's INBOX."""
    files = collect_files(notmuch_query)
    if not files:
        return 0

    tag_expr = '-trash +inbox'
    if unmark:
        tag_expr += ' -marked'
    notmuch.tag(tag_expr, notmuch_query)

    resolved: List[str] = []
    for f in files:
        r = _resolve_stale_path(f)
        if r is None:
            continue
        resolved.append(r)

    moves: List[Tuple[str, str]] = []
    for f in resolved:
        if not _is_trash_path(f):
            logger.debug('restore: not in trash folder: %s', f)
            continue

        result = _mail_file_account(f)
        if result is None:
            continue
        account, _ = result
        inbox_cur = os.path.join(
            os.path.expanduser(settings.mail_root), account, 'INBOX', 'cur')
        os.makedirs(inbox_cur, exist_ok=True)

        basename = _strip_uid_annotation(os.path.basename(f))
        moves.append((f, _unique_dest(os.path.join(inbox_cur, basename))))

    if moves:
        _get_worker().enqueue(moves)

    return len(moves)
