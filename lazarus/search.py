#     Lazarus - A fork of Dodo, a graphical, hackable email client based on notmuch
#     Copyright (C) 2021 - Aleks Kissinger
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
from __future__ import annotations
from typing import Optional, Any, Set

from PyQt6.QtCore import Qt, QAbstractItemModel, QModelIndex
from PyQt6.QtWidgets import QWidget, QLabel
from PyQt6.QtGui import QColor
import subprocess
import json
import logging

from . import settings
from . import notmuch
from . import keymap
from . import panel
from . import actions
from . import util
from . import style
from .protocols import PanelApp
from .thread_model import latest_message

logger = logging.getLogger(__name__)

columns = ['date', '#', 'from', 'subject', 'tags']


def render_thread_cell(thread_d: dict, col: str, role: int,
                       hide_tags: set | None = None,
                       hide_query: str = '') -> Any:
    """Render a single cell for a thread in a search view.

    :param thread_d: thread JSON dict from notmuch
    :param col: column name (``'date'``, ``'#'``, ``'from'``, ``'subject'``, ``'tags'``)
    :param role: Qt ``ItemDataRole``
    :param hide_tags: tags to suppress in the tag column (defaults to
                      ``settings.hide_tags``)
    :param hide_query: if non-empty, a ``'tag:X'``-style query whose tag
                       should be hidden (used by search panels)
    """
    if hide_tags is None:
        hide_tags = set(settings.hide_tags)

    if role == Qt.ItemDataRole.DisplayRole:
        if col == 'date':
            return thread_d['date_relative']
        elif col == 'from':
            return thread_d['authors']
        elif col == 'subject':
            return thread_d['subject']
        elif col == 'tags':
            tag_icons = []
            tags = util.sort_tags(thread_d['tags'])
            for t in tags:
                if t in hide_tags:
                    continue
                if hide_query and hide_query == 'tag:' + t:
                    continue
                tag_icons.append(
                    settings.tag_icons[t] if t in settings.tag_icons
                    else f'[{t}]')
            return ' '.join(tag_icons)
        elif col == '#':
            total = thread_d.get('total', 1)
            return f'\uf086 {total}' if total > 1 else ''

    elif role == Qt.ItemDataRole.FontRole:
        bold = ('unread' in thread_d['tags'] or 'flagged' in thread_d['tags'])
        if col == 'tags':
            return style.cell_font(
                settings.tag_font, settings.tag_font_size, bold=bold)
        return style.cell_font(
            settings.search_font, settings.search_font_size, bold=bold)

    elif role == Qt.ItemDataRole.ForegroundRole:
        for tag in settings.search_color_overrides.keys() & thread_d['tags']:
            if col in settings.search_color_overrides[tag]:
                return QColor(settings.search_color_overrides[tag][col])
        color = 'fg_' + col
        unread_color = 'fg_' + col + '_unread'
        flagged_color = 'fg_' + col + '_flagged'
        if 'unread' in thread_d['tags'] and unread_color in settings.theme:
            return style.theme_color(unread_color)
        elif 'flagged' in thread_d['tags'] and flagged_color in settings.theme:
            return style.theme_color(flagged_color)
        elif color in settings.theme:
            return style.theme_color(color)
        else:
            return style.theme_color('fg')

    elif role == Qt.ItemDataRole.ToolTipRole and col == 'tags':
        return ' '.join(thread_d['tags'])

    return None


class SearchModel(QAbstractItemModel):
    """A model containing the results of a search"""

    def __init__(self, q: str) -> None:
        super().__init__()
        self.q = q
        self.d: list = []
        self.threads: dict = {}
        self.json_str = ""
        self.num_threads = 0
        self.error_msg: str | None = None
        self.refresh()

    def refresh(self) -> None:
        """Refresh the model by (re-) running "notmuch search"."""
        logger.info("Beginning search refresh for '%s'", self.q)
        self.beginResetModel()
        try:
            self.json_str = notmuch.search_json(self.q)
            self.d = json.loads(self.json_str)
            self.error_msg = None
        except subprocess.CalledProcessError as e:
            # We keep the previous data, just add an error message on top.
            self.error_msg = f"notmuch: {e.stderr}"
        self.threads = {thread['thread']: i for i, thread in enumerate(self.d)}
        self.num_threads = len(self.d)
        self.endResetModel()

    def refresh_thread(self, thread: QModelIndex | str) -> None:
        """Refresh one thread's data in place when possible.

        Re-runs the search for just this thread and, if it still matches,
        replaces the row and emits ``dataChanged`` — no full model reset,
        so the tree keeps its selection, scroll position, and expanded
        state. Falls back to a full reset when the thread no longer
        matches the query (row removed) or notmuch errors.
        """
        if isinstance(thread, str):
            thread_id = thread
            row = self.threads[thread]
        else:
            tid = self.thread_id(thread)
            assert tid is not None
            thread_id = tid
            row = thread.row()

        logger.info("Search '%s': refreshing thread %s", self.q, thread_id)
        try:
            contents = json.loads(
                notmuch.search_json(f'{self.q} AND thread:{thread_id}'))
        except subprocess.CalledProcessError as e:
            self.error_msg = f"notmuch: {e.stderr}"
            return

        if len(contents) == 1 and row < len(self.d):
            if contents[0] == self.d[row]:
                return  # nothing changed
            self.d[row] = contents[0]
            self.threads = {t['thread']: i for i, t in enumerate(self.d)}
            self.num_threads = len(self.d)
            first = self.index(row, 0)
            last = self.index(row, len(columns) - 1)
            self.dataChanged.emit(first, last, [])
            logger.info("Thread %s refreshed in place", thread_id)
        else:
            # Thread dropped out of the query (or the search changed) —
            # full reset so the row removal is signalled correctly.
            logger.info("Thread %s left the query; full reset", thread_id)
            self.beginResetModel()
            self.d[row:row+1] = contents
            self.threads = {t['thread']: i for i, t in enumerate(self.d)}
            self.num_threads = len(self.d)
            self.endResetModel()

    def refresh_num_threads(self) -> None:
        """Only refresh the number of threads in the search, not the underlying data"""
        logger.info("Search '%s': Refreshing cached thread count", self.q)
        try:
            r = notmuch.run('count', '--output=threads', self.q, check=True)
            self.num_threads = int(r.stdout)
        except subprocess.CalledProcessError as e:
            # Just log the error and move on
            logger.warning("Error refreshing thread count: %s", e.stderr)

    def thread_json(self, index: QModelIndex) -> Optional[dict]:
        """Return a JSON object associated with the thread at the given model index"""

        row = index.row()
        if row >= 0 and row < len(self.d):
            return self.d[row]
        else:
            return None

    def thread_id(self, index: QModelIndex) -> Optional[str]:
        """Return the notmuch thread id associated with the thread at the given model index"""

        thread = self.thread_json(index)
        if thread and 'thread' in thread:
            return thread['thread']
        else:
            return None

    def data(self, index: QModelIndex, role: int=Qt.ItemDataRole.DisplayRole) -> Any:
        """Overrides `QAbstractItemModel.data` to populate a view with search results"""

        if index.row() >= len(self.d) or index.column() >= len(columns):
            return None

        thread_d = self.d[index.row()]
        col = columns[index.column()]
        return render_thread_cell(thread_d, col, role, hide_query=self.q)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int=Qt.ItemDataRole.DisplayRole) -> Any:
        """Overrides `QAbstractItemModel.headerData` to populate a view with column names"""

        if role == Qt.ItemDataRole.DisplayRole and section < len(columns):
            return columns[section]
        else:
            return None

    def index(self, row: int, column: int, parent: QModelIndex=QModelIndex()) -> QModelIndex:
        """Construct a `QModelIndex` for the given row and column"""

        if not self.hasIndex(row, column, parent): return QModelIndex()
        else: return self.createIndex(row, column, None)

    def columnCount(self, index: QModelIndex=QModelIndex()) -> int:
        """The number of columns"""

        return len(columns)

    def rowCount(self, index: QModelIndex=QModelIndex()) -> int:
        """The number of rows

        This is essentially an alias for :func:`num_threads`, but it also returns 0 if an index is
        given to tell Qt not to add any child items."""

        if not index or not index.isValid(): return self.num_threads
        else: return 0

    def parent(self, child: QModelIndex = QModelIndex()) -> Any:
        """Always return an invalid index, since there are no nested indices"""

        if not child: return super().parent()
        else: return QModelIndex()

class SearchPanel(actions.MarkableActionsMixin, panel.Panel):
    """A panel showing the results of a search

    This is used as the main entry point for the GUI, i.e. a search for "tag:inbox"."""

    def __init__(self, a: PanelApp, q: str, keep_open: bool=False, parent: Optional[QWidget]=None):
        self._dirty_content = False
        self._dirty_title = False
        super().__init__(a, keep_open, parent)
        self.set_keymap(keymap.search_keymap)
        self.q = q
        self._geometry_key = 'search_tree_geometry'
        self.tree = panel.HeaderInsetTreeView()
        self.error_view = QLabel()
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Per-item padding is now handled globally in themes.py so the date
        # column aligns with the header (~4px left). The global default of
        # 1px vertical preserves search_view_padding; override only if needed.
        # self.setStyleSheet(f'QTreeView::item {{ padding: {settings.search_view_padding}px }}')
        self.model = SearchModel(q)
        self.tree.setModel(self.model)
        self.model.modelReset.connect(self.on_data_refresh)
        lay = self.layout()
        if lay is not None:
            lay.addWidget(self.error_view)
            lay.addWidget(self.tree)
        self.tree.doubleClicked.connect(self.open_current_thread)
        self._setup_auto_open(self.tree)
        if self.model.rowCount() > 0:
            self.tree.setCurrentIndex(self.model.index(0, 0))
        self.on_data_refresh()
        self.restore_tree_geometry()

    # We want to split dirtyness into title-level and content-level
    # as updating the title data is much cheaper.
    @property
    def dirty(self) -> bool:
        return self._dirty_content

    @dirty.setter
    def dirty(self, value: bool) -> None:
        self._dirty_content = value
        self._dirty_title = value

    def on_data_refresh(self) -> None:
        if self.model.error_msg is None:
            self.error_view.hide()
        else:
            self.error_view.setText(self.model.error_msg)
            self.error_view.show()
        self.has_refreshed.emit()

    def _select_near_row(self, target_row: int) -> None:
        """Select the row at *target_row* if valid, else the last row."""
        count = self.model.rowCount()
        if count == 0:
            return
        row = min(target_row, count - 1)
        self.tree.setCurrentIndex(self.model.index(row, 0))

    def refresh(self) -> None:
        """Refresh the search listing and restore the selection by thread ID."""
        current_id = self.model.thread_id(self.tree.currentIndex())
        current_row = self.tree.currentIndex().row()
        self.model.refresh()
        self.restore_tree_geometry()
        # Restore by thread ID first (precise), fall back to row position
        if current_id and current_id in self.model.threads:
            self.tree.setCurrentIndex(
                self.model.index(self.model.threads[current_id], 0))
        else:
            self._select_near_row(current_row)
        # If the list is empty (last thread deleted), close the preview.
        if self.model.rowCount() == 0:
            self.app.main_window.clear_thread()
        super().refresh()

    def set_query(self, q: str) -> None:
        """Replace this panel's query and refresh in-place."""
        if not q.strip():
            return
        self.q = q
        self.model = SearchModel(q)
        self.tree.setModel(self.model)
        self.model.modelReset.connect(self.on_data_refresh)
        self.dirty = True
        self.refresh()

    def update_thread(self, thread_id: str, msg_id: str|None= None) -> None:
        logger.info("Search '%s': updating thread '%s'", self.q, thread_id)
        # If any thread is unknown to the current search, just do a full refresh
        if thread_id not in self.model.threads:
            logger.info("Search '%s': unknown thread", self.q)
            if self.hasFocus():
                self.refresh()
            else:
                self.dirty = True
        else:
            current = self.tree.currentIndex()
            self.model.refresh_thread(thread_id)
            if current.row() >= self.model.num_threads:
                self.last_thread()
            else:
                self.tree.setCurrentIndex(current)

    def title(self) -> str:
        """Use the configured tab title"""
        logger.info("Search '%s': updating title", self.q)
        if self._dirty_title:
            self.model.refresh_num_threads()
            self._dirty_title = False
        return settings.search_title_format.format(
            query=self.q, num_threads=self.model.num_threads
        )

    def next_thread(self, unread: bool=False) -> None:
        """Select the next thread in the search

        :param unread: if True, this will jump to the next unread thread
        """

        row = self.tree.currentIndex().row()
        while True:
            row += 1
            i = self.model.index(row, 0)
            thread = self.model.thread_json(i)
            if not thread:
                break
            elif not unread or (thread and 'tags' in thread and 'unread' in thread['tags']):
                self.tree.setCurrentIndex(i)
                break

    def previous_thread(self, unread: bool=False) -> None:
        """Select the previous thread in the search

        :param unread: if True, this will jump to the previous unread thread
        """

        row = self.tree.currentIndex().row()
        while True:
            row -= 1
            i = self.model.index(row, 0)
            thread = self.model.thread_json(i)
            if not thread:
                break
            elif not unread or ('tags' in thread and 'unread' in thread['tags']):
                self.tree.setCurrentIndex(i)
                break

    def first_thread(self) -> None:
        """Select the first thread in the search"""

        ix = self.model.index(0, 0)
        if self.model.checkIndex(ix):
            self.tree.setCurrentIndex(ix)

    def last_thread(self) -> None:
        """Select the last thread in the search"""

        ix = self.model.index(self.model.rowCount()-1, 0)
        if self.model.checkIndex(ix):
            self.tree.setCurrentIndex(ix)

    def open_current_thread(self) -> None:
        """Open the selected thread"""

        thread_id = self.model.thread_id(self.tree.currentIndex())
        if thread_id:
            self.app.open_thread(thread_id, self.model.q)

    # -- reply / forward from the list --------------------------------------
    # These act on the selected thread's most recent email without opening
    # the preview (r / R / C-y from the search list).

    def _thread_latest_message(self) -> Optional[dict]:
        """Fetch the most recent message of the selected thread."""
        thread_id = self._current_thread_id()
        if not thread_id:
            return None
        try:
            r = notmuch.run('show', '--exclude=false', '--format=json',
                            '--', f'thread:{thread_id}', check=True)
            data = json.loads(r.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError, IndexError):
            return None
        if not data:
            return None
        return latest_message(data[0])

    def reply(self, to_all: bool = True) -> None:
        """Reply to the selected thread's most recent email."""
        msg = self._thread_latest_message()
        if msg is None:
            self.app.status_message('No message to reply to', 'warning')
            return
        self.app.open_compose(
            mode='replyall' if to_all else 'reply', msg=msg)

    def forward(self) -> None:
        """Forward the selected thread's most recent email."""
        msg = self._thread_latest_message()
        if msg is None:
            self.app.status_message('No message to forward', 'warning')
            return
        self.app.open_compose(mode='forward', msg=msg)

    # -- MarkableActionsMixin hooks -------------------------------------
    # tag_thread, toggle_thread_tag, archive_thread, delete_thread, and
    # archive_to_local are provided by actions.MarkableActionsMixin.

    def _marked_query(self) -> str:
        return f'tag:marked AND ({self.q})'

    def _current_thread_id(self) -> Optional[str]:
        return self.model.thread_id(self.tree.currentIndex())

    def _current_thread_tags(self) -> Optional[Set[str]]:
        thread = self.model.thread_json(self.tree.currentIndex())
        if thread is None:
            return None
        return set(thread.get('tags', []))

    def _advance_selection(self) -> None:
        """No-op: ``refresh()`` handles position restoration via
        ``_select_near_row`` fallback when the deleted thread ID is
        no longer in the model."""

