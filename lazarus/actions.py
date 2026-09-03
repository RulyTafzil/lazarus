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
"""Shared email actions used by search and thread panels.

This module centralises delete, archive, and file-move operations that
were previously duplicated across SearchPanel and ThreadPanel.

File moves run on a background ``QThread`` so rapid successive bulk
actions queue up instead of interrupting each other.  Tagging and
notmuch queries stay synchronous so the UI reflects changes instantly.
``notmuch new`` is fired only after a batch of moves has actually
landed on disk (via the worker's ``batch_done`` signal), not
immediately after enqueueing, so it never races the renames.
"""

from __future__ import annotations
import os
import re
import logging
import queue
import threading
from typing import Callable, Set, Optional, List, Tuple, Literal

from PyQt6.QtCore import QThread, pyqtSignal

from . import settings
from . import notmuch
from .protocols import PanelApp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Background worker for file moves
# ---------------------------------------------------------------------------

_SHUTDOWN = object()

class _BulkMoveWorker(QThread):
    """Serialises file moves and runs ``notmuch new`` after each batch."""

    batch_done = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.queue: queue.Queue[Tuple[str, str] | object | None] = queue.Queue()
        self._lock = threading.Lock()
        self._batches_pending = 0
        self._shutting_down = False

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
        self.wait(timeout_ms)

    def run(self) -> None:
        while True:
            item = self.queue.get()
            if item is _SHUTDOWN:
                return
            if item is None:
                with self._lock:
                    self._batches_pending -= 1
                try:
                    notmuch.new()
                except Exception as e:
                    logger.warning('notmuch new failed: %s', e)
                self.batch_done.emit()
                continue
            if not isinstance(item, tuple):
                continue
            src, dst = item
            if not os.path.exists(src):
                logger.debug('skip (already moved): %s', src)
                continue
            try:
                os.rename(src, dst)
            except OSError as e:
                logger.warning('move failed: %s → %s: %s', src, dst, e)


# Singleton worker, started on first use and kept alive for the session.
_worker: _BulkMoveWorker | None = None

# App-level slot run after every completed move batch, alongside the
# worker's own ``notmuch new`` (in practice the controller's
# refresh_panels).  Kept module-level so a recreated worker is wired
# identically — the connection used to be made once, at startup, against
# the *first* worker instance and would have been silently lost if the
# worker were ever recreated.
_batch_done_listener: Optional[Callable[[], None]] = None


def set_batch_done_listener(fn: Optional[Callable[[], None]]) -> None:
    """Register (or clear, with None) the app-level slot run after each
    completed move batch.

    If a worker is already running the slot is connected immediately;
    otherwise :func:`_get_worker` connects it when the worker starts.
    """
    global _batch_done_listener
    _batch_done_listener = fn
    if fn is not None and _worker is not None and _worker.isRunning():
        _worker.batch_done.connect(fn)


def _run_notmuch_new() -> None:
    """Legacy helper maintained for backward compatibility.
    Re-indexing is performed on the worker thread prior to emitting batch_done.
    """
    pass


def _get_worker() -> _BulkMoveWorker:
    global _worker
    if _worker is None or _worker.isFinished():
        _worker = _BulkMoveWorker()
        _worker.batch_done.connect(_run_notmuch_new)
        if _batch_done_listener is not None:
            _worker.batch_done.connect(_batch_done_listener)
        _worker.start()
    return _worker


def shutdown_worker() -> None:
    """Call from aboutToQuit — joins the worker if alive."""
    if _worker is not None and _worker.isRunning():
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
    """Split a mail file path into (account, rest_of_path).

    Returns None if the path doesn't live under the mail root.  Pass
    *mail_root* explicitly for a pure, deterministic mapping; otherwise
    it falls back to ``settings.mail_root``.
    """
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
    """Trash cur/ directory for *account* (pure path; not created).

    Prefers the Gmail-style ``[Gmail]/Trash`` folder when it exists,
    falling back to a plain ``Trash`` folder.
    """
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
    """Return *path* unchanged if it does not exist, otherwise append a
    counter (``.1``, ``.2``, ...) to make it unique.

    Prevents silent data loss when two source files collide on the same
    destination basename after UID-stripping.
    """
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
    """If *f* doesn't exist on disk, look for a file with the same
    basename stem in this Maildir's 'cur' and 'new' subdirectories.

    Handles both mbsync flag-suffix renames (':2,' -> ':2,S') and
    notmuch's synchronize_flags moving a message out of 'new/' into
    'cur/' the first time any flag (e.g. Seen/unread) is written.
    """
    if os.path.exists(f):
        return f
    parent = os.path.dirname(f)
    stem = os.path.basename(f)
    stem_base = stem.rsplit(':2,', 1)[0] if ':2,' in stem else stem

    candidates = [parent]
    if os.path.basename(parent) in ('new', 'cur'):
        maildir_base = os.path.dirname(parent)
        for sub in ('cur', 'new'):
            sibling = os.path.join(maildir_base, sub)
            if sibling not in candidates:
                candidates.append(sibling)

    for directory in candidates:
        try:
            for entry in os.listdir(directory):
                if entry.startswith(stem_base):
                    return os.path.join(directory, entry)
        except OSError:
            pass
    return None

# ---------------------------------------------------------------------------
# Public API
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
    """Compute ``(src, dst)`` moves to each file's account Trash folder.

    Pure planning: nothing is created, moved, or tagged here.  *files*
    must already be resolved (see :func:`collect_files`); *mail_root* is
    passed explicitly so the per-account mapping is deterministic and
    testable.  Destination directories are computed (not created) by
    :func:`_trash_dir_path`.
    """
    mail_root = os.path.expanduser(mail_root)
    moves: List[Tuple[str, str]] = []
    for f in files:
        result = _mail_file_account(f, mail_root)
        if result is None:
            continue
        account, _ = result
        trash_dir = _trash_dir_path(account, mail_root)
        basename = _strip_uid_annotation(os.path.basename(f))
        moves.append((f, _unique_dest(os.path.join(trash_dir, basename))))
    return moves


def plan_archive_moves(files: List[str],
                       archive_dir: str) -> List[Tuple[str, str]]:
    """Compute ``(src, dst)`` moves into ``archive_dir/cur``.

    Pure: no ``mkdir``, no move, no tag.  Files already inside the
    target folder are skipped.
    """
    archive_cur = os.path.join(os.path.expanduser(archive_dir), 'cur')
    moves: List[Tuple[str, str]] = []
    for f in files:
        if f.startswith(archive_cur + os.sep):
            logger.debug('skip (already in target): %s', os.path.basename(f))
            continue
        basename = _strip_uid_annotation(os.path.basename(f))
        moves.append((f, _unique_dest(os.path.join(archive_cur, basename))))
    return moves


def move_to_trash(notmuch_query: str) -> int:
    """Tag ``+trash`` and move matching files to the Trash folder.

    Tagging is synchronous (instant UI feedback).  File moves are
    enqueued to a background thread so rapid successive calls don't
    interrupt each other.

    Returns the number of *files moved* (happens asynchronously).
    """
    # Search BEFORE tagging — tag changes may alter query matching.
    files = collect_files(notmuch_query)
    if not files:
        return 0

    notmuch.tag('+trash -inbox -unread', notmuch_query, exclude_marked=True)

    # Re-resolve after tagging — notmuch.tag may have renamed the file
    # (synchronize_flags=true renames files when tags change), then hand
    # the resolved list to the pure planner for per-account mapping.
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


# ---------------------------------------------------------------------------
# Shared panel mixin
# ---------------------------------------------------------------------------

class MarkableActionsMixin:
    """Shared "act on marked threads, or fall back to the current thread"
    logic for :class:`~lazarus.search.SearchPanel`.

    The panel previously implemented ``tag_thread``/``toggle_thread_tag``/
    ``archive_thread``/``delete_thread``/``archive_to_local`` inline;
    the logic is factored into three small hooks subclasses must implement:

    - :func:`_marked_query`
    - :func:`_current_thread_id`
    - :func:`_current_thread_tags`

    Subclasses may also override :func:`_advance_selection` to move the
    cursor before a destructive action (delete/archive).
    """

    app: PanelApp  # provided by the concrete panel (SearchPanel)

    def _marked_query(self) -> str:
        """Notmuch query matching "marked" threads in this panel's scope."""
        raise NotImplementedError

    def _has_marked_threads(self) -> bool:
        """Return True if any threads are marked in this panel's scope.

        Default falls back to checking _marked_query, but panels with
        in-memory rows (e.g. SearchPanel) should override with an instant check.
        """
        try:
            return notmuch.count(self._marked_query()) > 0
        except Exception:
            return False

    def _current_thread_id(self) -> Optional[str]:
        """Thread id of the currently selected row, or None."""
        raise NotImplementedError

    def _current_thread_tags(self) -> Optional[Set[str]]:
        """Tags of the currently selected thread, or None if unavailable."""
        raise NotImplementedError

    def _advance_selection(self) -> None:
        """Move the cursor before a destructive single-thread action.

        Called by ``delete_thread``, ``archive_thread``, and
        ``archive_to_local`` when operating on the current thread
        (not a marked batch).  Default is a no-op; panels that want
        the cursor to advance should override this.
        """

    def tag_thread(self, tag_expr: str,
                   mode: Literal['tag', 'tag marked'] = 'tag') -> None:
        """Apply the given tag expression to the selected thread, or to
        all marked threads in this panel's scope.

        A tag expression is a string consisting of one more statements
        of the form "+TAG" or "-TAG" to add or remove TAG, respectively,
        separated by whitespace.
        """
        if not ('+' in tag_expr or '-' in tag_expr):
            tag_expr = '+' + tag_expr

        if mode == 'tag marked':
            r = notmuch.tag(tag_expr, self._marked_query(), exclude_marked=True)
            if r.returncode != 0:
                self.app.status_message(
                    f'Tag error: {r.stderr.strip()[:200]}', 'error')
                return
            self.app.refresh_panels()
        else:
            thread_id = self._current_thread_id()
            if not thread_id:
                return
            r = notmuch.tag(tag_expr, 'thread:' + thread_id)
            if r.returncode != 0:
                self.app.status_message(
                    f'Tag error: {r.stderr.strip()[:200]}', 'error')
                return
            self.app.update_single_thread(thread_id)

    def toggle_thread_tag(self, tag: str) -> None:
        """Toggle the given tag on the currently selected thread."""
        tags = self._current_thread_tags()
        if tags is None:
            return
        tag_expr = ('-' + tag) if tag in tags else ('+' + tag)
        self.tag_thread(tag_expr)

    def archive_thread(self) -> None:
        """Archive (``-inbox -unread``) all marked threads in this
        panel's scope, or the current thread if none are marked."""
        if self._has_marked_threads():
            marked_query = self._marked_query()
            r = notmuch.tag('-inbox -unread', marked_query, exclude_marked=True)
            if r.returncode != 0:
                self.app.status_message(
                    f'Archive error: {r.stderr.strip()[:200]}', 'error')
                return
            self.app.refresh_panels()
            self.app.status_message('Archived marked', 'info')
            return

        thread_id = self._current_thread_id()
        if not thread_id:
            return
        tags = self._current_thread_tags()
        if tags is None:
            return
        if check_archive_refused(tags):
            self.app.status_message(
                'Archive refused: thread has no tags beyond inbox/unread',
                'warning')
            return
        self._advance_selection()
        r = notmuch.tag('-inbox -unread', 'thread:' + thread_id)
        if r.returncode != 0:
            self.app.status_message(
                f'Archive error: {r.stderr.strip()[:200]}', 'error')
            return
        self.app.update_single_thread(thread_id)

    def delete_thread(self) -> None:
        """Move all marked threads in this panel's scope to Trash, or
        the current thread if none are marked."""
        if self._has_marked_threads():
            marked_query = self._marked_query()
            moved = move_to_trash(marked_query)
            if moved > 0:
                self.app.refresh_panels()
                self.app.status_message('Deleted marked', 'info')
                return

        self._advance_selection()
        thread_id = self._current_thread_id()
        if not thread_id:
            return
        move_to_trash('thread:' + thread_id)
        self.app.update_single_thread(thread_id)
        self.app.status_message('Moved to trash', 'info')

    def restore_thread_from_trash(self) -> None:
        """Move the current thread (or all marked threads) from Trash
        back to INBOX, undoing a soft-delete."""
        if self._has_marked_threads():
            marked_query = self._marked_query()
            moved = restore_from_trash(
                f'tag:trash AND ({marked_query})')
            if moved > 0:
                self.app.refresh_panels()
                self.app.status_message(
                    f'Restored {moved} file{"s" if moved != 1 else ""} '
                    f'from trash', 'info')
                return

        self._advance_selection()
        thread_id = self._current_thread_id()
        if not thread_id:
            return
        moved = restore_from_trash(
            f'tag:trash AND thread:{thread_id}')
        if moved == 0:
            self.app.status_message(
                'Not in trash', 'warning')
            return
        self.app.update_single_thread(thread_id)
        self.app.status_message(
            f'Restored {moved} file{"s" if moved != 1 else ""} '
            f'from trash', 'info')

    def archive_to_local(self) -> None:
        """Move all marked threads in this panel's scope to the local
        Archive maildir, or the current thread if none are marked."""
        if self._has_marked_threads():
            marked_query = self._marked_query()
            moved = move_to_archive(marked_query)
            if moved > 0:
                self.app.refresh_panels()
                self.app.status_message('Archived marked to local', 'info')
                return

        thread_id = self._current_thread_id()
        if not thread_id:
            return
        tags = self._current_thread_tags()
        if tags is None:
            return
        if check_archive_refused(tags):
            self.app.status_message(
                'Archive refused: thread has no tags beyond inbox/unread',
                'warning')
            return
        self._advance_selection()
        move_to_archive('thread:' + thread_id)
        self.app.update_single_thread(thread_id)
        self.app.status_message('Archived to local', 'info')


def move_to_archive(notmuch_query: str) -> int:
    """Tag ``-inbox -unread`` and move matching files to local Archive.

    Tagging is synchronous.  File moves are enqueued to a background
    thread.

    Returns the number of *files found*.
    """
    # Search BEFORE tagging: notmuch_query often includes tag:inbox or
    # tag:unread (e.g. the default 'tag:inbox' view), and those are
    # exactly the tags we're about to remove. Re-searching afterwards
    # would match nothing, so we move this exact file list rather than
    # re-querying after the tags changed.
    files = collect_files(notmuch_query)
    if not files:
        return 0
    notmuch.tag('-inbox -unread', notmuch_query, exclude_marked=True)
    # Errors here are non-fatal — file moves proceed regardless.

    return move_specific_files(files, os.path.expanduser(settings.archive_dir))


def move_specific_files(files: List[str], target_dir: str) -> int:
    """Move an already-resolved list of file paths into
    ``target_dir/cur/``.

    This is the only file-move entry point: callers collect the file
    list with :func:`collect_files` *before* any tagging that might
    change which files a fresh search would match (see
    :func:`move_to_archive` and :func:`lazarus.rules.apply_rules`).

    :returns: the number of *files moved* (happens asynchronously)
    """
    target_dir = os.path.expanduser(target_dir)
    target_cur = os.path.join(target_dir, 'cur')
    os.makedirs(target_cur, exist_ok=True)

    # Re-resolve paths — notmuch.tag may have renamed files since
    # collect_files() ran (synchronize_flags=true renames files on disk
    # when tags like 'unread' change), then let the pure planner map
    # them (it skips anything already inside the target).
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


def expunge_trash() -> int:
    """Add the Maildir ``T`` (Trashed) flag to every file matching
    ``tag:trash`` that lives inside a Trash folder.

    This is the irreversible step after a soft-delete — it tells the
    IMAP server (via mbsync on the next sync) to expunge the message.
    Files already marked ``T`` are skipped.

    :returns: the number of files marked ``T``
    """
    files = collect_files('tag:trash')

    tagged = 0
    for f in files:
        if not _is_trash_path(f):
            logger.debug('expunge: not in trash folder: %s', f)
            continue

        dirname = os.path.dirname(f)
        basename = os.path.basename(f)

        # Parse Maildir info suffix: base:2,FLAGS
        if ':2,' in basename:
            base, flags = basename.rsplit(':2,', 1)
            if 'T' in flags:
                continue  # already trashed
            new_basename = f'{base}:2,{flags}T'
        else:
            new_basename = basename + ':2,T'

        new_path = os.path.join(dirname, new_basename)
        try:
            os.rename(f, new_path)
        except OSError as e:
            logger.warning('expunge: rename failed: %s → %s: %s',
                           f, new_path, e)
            continue
        tagged += 1

    if tagged:
        notmuch.tag('-trash', 'tag:trash')

    return tagged


def restore_from_trash(notmuch_query: str) -> int:
    """Move files matching *notmuch_query* from their Trash folder back
    to the same account's INBOX and tag ``-trash +inbox``.

    This is the inverse of :func:`move_to_trash` — it undoes a
    soft-delete.  Files that aren't actually in a Trash folder are
    skipped.

    :returns: the number of files moved
    """
    files = collect_files(notmuch_query)

    moves: List[Tuple[str, str]] = []
    for f in files:
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
        notmuch.tag('-trash +inbox', notmuch_query)
        _get_worker().enqueue(moves)

    return len(moves)
