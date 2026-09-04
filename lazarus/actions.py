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
"""UI email actions for desktop search and thread panels.

This module provides Qt-specific panel actions (MarkableActionsMixin, Actions)
bridged to the headless domain primitives in lazarus.core.actions.
"""

from __future__ import annotations
import logging
import os
from typing import Callable, List, Literal, Optional, Set, Tuple

from PyQt6.QtCore import QObject, pyqtSignal

from . import notmuch
from .protocols import PanelApp

from .core.actions import (
    _BulkMoveWorker,
    _get_worker as _core_get_worker,
    get_worker,
    shutdown_worker,
    set_batch_done_listener as _core_set_batch_done_listener,
    collect_files,
    plan_trash_moves,
    plan_archive_moves,
    move_to_trash,
    move_to_archive,
    move_specific_files,
    expunge_trash,
    restore_from_trash,
    check_archive_refused,
    _strip_uid_annotation,
    _unique_dest,
    _resolve_stale_path,
    _mail_file_account,
    _trash_dir_path,
    _find_trash_dir,
    _find_archive_dir,
    _is_trash_path,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Qt Signal Bridge for batch_done
# ---------------------------------------------------------------------------

class _QtBatchDoneBridge(QObject):
    """Bridge background thread completion into the Qt main event loop."""
    batch_done = pyqtSignal()


_bridge = _QtBatchDoneBridge()
_core_set_batch_done_listener(_bridge.batch_done.emit)


def set_batch_done_listener(fn: Optional[Callable[[], None]]) -> None:
    """Register or clear the app-level slot run after each completed move batch."""
    try:
        _bridge.batch_done.disconnect()
    except (TypeError, RuntimeError):
        pass
    if fn is not None:
        _bridge.batch_done.connect(fn)


def _run_notmuch_new() -> None:
    """Legacy helper maintained for backward compatibility."""
    pass


def _get_worker() -> _BulkMoveWorker:
    w = _core_get_worker()
    w.batch_done = _bridge.batch_done  # type: ignore[attr-defined]
    return w


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
            from .client import get_client, is_ned_active
            if is_ned_active():
                return get_client().count(self._marked_query()) > 0
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

        from .client import get_client, is_ned_active
        if is_ned_active():
            client = get_client()
            add_tags = [t[1:] for t in tag_expr.split() if t.startswith('+')]
            remove_tags = [t[1:] for t in tag_expr.split() if t.startswith('-')]
            if mode == 'tag marked':
                ok = client.modify_tags(self._marked_query(), add=add_tags, remove=remove_tags)
                if not ok:
                    self.app.status_message('Tag error', 'error')
                    return
                self.app.refresh_panels()
            else:
                thread_id = self._current_thread_id()
                if not thread_id:
                    return
                ok = client.modify_tags(f'thread:{thread_id}', add=add_tags, remove=remove_tags)
                if not ok:
                    self.app.status_message('Tag error', 'error')
                    return
                self.app.update_single_thread(thread_id)
            return

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
        from .client import get_client, is_ned_active
        if is_ned_active():
            client = get_client()
            if self._has_marked_threads():
                marked_query = self._marked_query()
                ok = client.archive_thread(marked_query)
                if not ok:
                    self.app.status_message('Archive error', 'error')
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
            ok = client.archive_thread(thread_id)
            if not ok:
                self.app.status_message('Archive error', 'error')
                return
            self.app.update_single_thread(thread_id)
            return

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
        from .client import get_client, is_ned_active
        if is_ned_active():
            client = get_client()
            if self._has_marked_threads():
                marked_query = self._marked_query()
                ok = client.trash_thread(marked_query)
                if ok:
                    self.app.refresh_panels()
                    self.app.status_message('Deleted marked', 'info')
                    return

            self._advance_selection()
            thread_id = self._current_thread_id()
            if not thread_id:
                return
            client.trash_thread(thread_id)
            self.app.update_single_thread(thread_id)
            self.app.status_message('Moved to trash', 'info')
            return

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
        from .client import get_client, is_ned_active
        if is_ned_active():
            client = get_client()
            if self._has_marked_threads():
                marked_query = self._marked_query()
                ok = client.untrash_thread(marked_query)
                if ok:
                    self.app.refresh_panels()
                    self.app.status_message('Restored from trash', 'info')
                    return

            self._advance_selection()
            thread_id = self._current_thread_id()
            if not thread_id:
                return
            ok = client.untrash_thread(thread_id)
            self.app.update_single_thread(thread_id)
            self.app.status_message('Restored from trash', 'info')
            return

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
        from .client import get_client, is_ned_active
        if is_ned_active():
            client = get_client()
            if self._has_marked_threads():
                marked_query = self._marked_query()
                ok = client.archive_thread(marked_query)
                if ok:
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
            client.archive_thread(thread_id)
            self.app.update_single_thread(thread_id)
            self.app.status_message('Archived to local', 'info')
            return

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

