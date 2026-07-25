#     Dodo - A graphical, hackable email client based on notmuch
#     Copyright (C) 2021 - Aleks Kissinger
#
# This file is part of Dodo
#
# Dodo is free software: you can redistribute it and/or modify
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
# along with Dodo. If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations
from typing import Optional, Any, List, Tuple, Literal, Set

from PyQt6.QtCore import Qt, QAbstractItemModel, QModelIndex, QSettings, QTimer
from PyQt6.QtWidgets import QTreeView, QHeaderView
from PyQt6.QtGui import QFont, QColor

import logging

from . import app
from . import settings
from . import keymap
from . import panel
from . import actions
from .search import SearchModel, columns, render_thread_cell

logger = logging.getLogger(__name__)


class DashboardModel(QAbstractItemModel):
    """A flat model mixing section headers and search result rows.

    Headers are non-leaf rows that span all columns.
    Thread rows are search results from per-section SearchModel instances.
    """

    def __init__(self, queries: List[Tuple[str, str]], max_items: Optional[int] = None):
        super().__init__()
        self.queries = queries
        self.max_items = max_items
        self.sections: List[Tuple[str, str, SearchModel]] = []
        self.rows: List[Tuple[str, Any]] = []  # ('header', (label, model)) or ('thread', dict)
        self._build()

    def _build(self) -> None:
        self.sections = []
        self.rows = []
        for label, query_str in self.queries:
            model = SearchModel(query_str)
            self.sections.append((label, query_str, model))
            self.rows.append(('header', (label, model)))
            items = model.d[:self.max_items] if self.max_items else model.d
            for thread in items:
                self.rows.append(('thread', thread))

    def refresh(self) -> None:
        """Refresh all section models and rebuild rows."""
        self.beginResetModel()
        for _, _, model in self.sections:
            model.refresh()
        self.rows = self._rebuild_rows()
        self.endResetModel()

    def refresh_thread(self, thread_id: str) -> None:
        """Refresh the data for a single thread if present."""
        for _, _, model in self.sections:
            if thread_id in model.threads:
                model.refresh_thread(thread_id)
                self._rebuild_from_sections()
                return

    def refresh_num_threads(self) -> None:
        """Only refresh counts without full rebuild."""
        for _, _, model in self.sections:
            model.refresh_num_threads()

    def _rebuild_rows(self) -> List[Tuple[str, Any]]:
        """Rebuild the flat row list from section models without re-querying."""
        rows: List[Tuple[str, Any]] = []
        for label, _, model in self.sections:
            rows.append(('header', (label, model)))
            items = model.d[:self.max_items] if self.max_items else model.d
            for thread in items:
                rows.append(('thread', thread))
        return rows

    def _rebuild_from_sections(self) -> None:
        """Rebuild rows from section models without re-querying."""
        self.beginResetModel()
        self.rows = self._rebuild_rows()
        self.endResetModel()

    def thread_id(self, index: QModelIndex) -> Optional[str]:
        """Return the notmuch thread ID for the row at index."""
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self.rows):
            return None
        typ, data = self.rows[row]
        if typ == 'thread' and 'thread' in data:
            return data['thread']
        return None

    def thread_data(self, index: QModelIndex) -> Optional[dict]:
        """Return the thread dict for the row at index, or None if it is a header."""
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self.rows):
            return None
        typ, data = self.rows[row]
        return data if typ == 'thread' else None

    def is_header(self, row: int) -> bool:
        """Check if the given row is a section header."""
        if row < 0 or row >= len(self.rows):
            return True
        return self.rows[row][0] == 'header'

    def _header_model_for_row(self, row: int) -> Optional[SearchModel]:
        """Return the SearchModel associated with the nearest preceding header."""
        for r in range(row, -1, -1):
            if self.rows[r][0] == 'header':
                return self.rows[r][1][1]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        global columns
        if not index.isValid():
            return None
        row = index.row()
        col_idx = index.column()
        if row < 0 or row >= len(self.rows) or col_idx >= len(columns):
            return None

        typ, data = self.rows[row]

        if typ == 'header':
            label, model = data
            if role == Qt.ItemDataRole.DisplayRole:
                if col_idx == 0:
                    icon = settings.tag_icons.get(label, '')
                    count = model.num_threads
                    if icon:
                        return f'{icon}  {label} ({count})'
                    return f'{label} ({count})'
                return ''
            elif role == Qt.ItemDataRole.FontRole:
                font = QFont(settings.search_font, settings.search_font_size)
                font.setBold(True)
                return font
            elif role == Qt.ItemDataRole.ForegroundRole:
                return QColor(settings.theme['fg_bright'])

        else:  # thread
            return render_thread_cell(data, columns[col_idx], role)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        global columns
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section < len(columns):
                return columns[section]
        return None

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        return self.createIndex(row, column, None)

    def parent(self, child: QModelIndex = QModelIndex()) -> QModelIndex:
        return QModelIndex()

    def columnCount(self, index: QModelIndex = QModelIndex()) -> int:
        global columns
        return len(columns)

    def rowCount(self, index: QModelIndex = QModelIndex()) -> int:
        if not index.isValid():
            return len(self.rows)
        return 0

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        row = index.row()
        if row < len(self.rows) and self.rows[row][0] == 'header':
            return Qt.ItemFlag.ItemIsEnabled  # not selectable
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class DashboardPanel(actions.MarkableActionsMixin, panel.Panel):
    """A panel showing multiple search queries in a single scrollable view.

    :param a: The Dodo application instance
    :param queries: List of (label, query_string) tuples
    :param max_items: Maximum items to show per section (None = no limit)
    :param keep_open: If True, prevent closing this panel
    """

    def __init__(self, a: app.Dodo, queries: Optional[List[Tuple[str, str]]] = None,
                 max_items: Optional[int] = None, keep_open: bool = False,
                 parent: Optional[QWidget] = None):
        super().__init__(a, keep_open, parent)
        self.set_keymap(keymap.dashboard_keymap)

        if queries is None:
            queries = settings.dashboard_queries
        if max_items is None:
            max_items = settings.dashboard_max_items

        self.queries = queries
        self.max_items = max_items

        self.tree = QTreeView()
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tree.setIndentation(0)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(False)
        self.setStyleSheet(f'QTreeView::item {{ padding: {settings.search_view_padding}px }}')

        self.model = DashboardModel(queries, max_items)
        self.tree.setModel(self.model)
        self.tree.doubleClicked.connect(self.open_current_thread)
        self._setup_auto_open(self.tree)

        # Span header rows across all columns
        self.tree.model().modelReset.connect(self._span_headers)

        # Restore column widths from previous session
        self.conf = QSettings("dodo", "dodo")
        self.restore_tree_geometry()

        self.layout().addWidget(self.tree)

        # Debounce dashboard refreshes — avoid N notmuch calls on rapid tag changes
        self._refresh_timer = QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(500)
        self._refresh_timer.timeout.connect(self._debounced_refresh)
        self._refresh_pending_row: int | None = None

        # Select first selectable row
        self._select_first_thread()
        self._span_headers()
        self.on_data_refresh()

    def _span_headers(self) -> None:
        """Make header rows span all columns."""
        for row in range(self.model.rowCount()):
            if self.model.is_header(row):
                idx = self.model.index(row, 0)
                self.tree.setFirstColumnSpanned(row, QModelIndex(), True)

    def restore_tree_geometry(self) -> None:
        """Restore column widths from previous session."""
        tree_geometry = self.conf.value("dashboard_tree_geometry")
        if tree_geometry:
            self.tree.header().restoreState(tree_geometry)

    def save_tree_geometry(self) -> None:
        """Save column widths for next session."""
        self.conf.setValue("dashboard_tree_geometry", self.tree.header().saveState())

    def before_close(self) -> bool:
        """Save geometry before closing."""
        self.save_tree_geometry()
        return super().before_close()

    def _advance_past_current(self) -> int:
        """Advance selection to the next non-header row and return its
        row index.  If already on the last item, moves to the previous.
        """
        row = self.tree.currentIndex().row()
        next_r = self._next_row(row)
        if next_r != row:
            self.tree.setCurrentIndex(self.model.index(next_r, 0))
            return next_r
        prev_r = self._prev_row(row)
        if prev_r != row:
            self.tree.setCurrentIndex(self.model.index(prev_r, 0))
            return prev_r
        return row

    def _select_near_row(self, target_row: int) -> None:
        """Select the nearest non-header row at or after *target_row*.

        Falls back to the last thread if *target_row* is out of range.
        """
        for r in range(target_row, self.model.rowCount()):
            if not self.model.is_header(r):
                self.tree.setCurrentIndex(self.model.index(r, 0))
                return
        self._select_last_thread()

    def _select_last_thread(self) -> None:
        """Select the last non-header row."""
        for row in range(self.model.rowCount() - 1, -1, -1):
            if not self.model.is_header(row):
                self.tree.setCurrentIndex(self.model.index(row, 0))
                return

    def _select_first_thread(self) -> None:
        """Select the first non-header row."""
        for row in range(self.model.rowCount()):
            if not self.model.is_header(row):
                idx = self.model.index(row, 0)
                self.tree.setCurrentIndex(idx)
                break

    def title(self) -> str:
        return 'dashboard'

    def on_data_refresh(self) -> None:
        self.model.modelReset.connect(lambda: self.has_refreshed.emit())

    def refresh(self) -> None:
        current_id = self.model.thread_id(self.tree.currentIndex())
        self.model.refresh()

        # Restore selection
        if current_id:
            for row in range(self.model.rowCount()):
                if self.model.thread_id(self.model.index(row, 0)) == current_id:
                    self.tree.setCurrentIndex(self.model.index(row, 0))
                    break
        else:
            self._select_first_thread()

        super().refresh()

    def update_thread(self, thread_id: str, msg_id: Optional[str] = None) -> None:
        """Schedule a debounced refresh — avoids N notmuch calls on rapid changes."""
        if self.hasFocus():
            self._refresh_timer.start()
        else:
            self.dirty = True

    def _debounced_refresh(self) -> None:
        """Perform the actual refresh after the debounce period."""
        self.model.refresh()
        pending = self._refresh_pending_row
        self._refresh_pending_row = None
        if pending is not None:
            self._select_near_row(pending)
        else:
            self._select_first_thread()
        self.has_refreshed.emit()

    def _next_row(self, current: int) -> int:
        """Find the next non-header row after current."""
        for r in range(current + 1, self.model.rowCount()):
            if not self.model.is_header(r):
                return r
        return current

    def _prev_row(self, current: int) -> int:
        """Find the previous non-header row before current."""
        for r in range(current - 1, -1, -1):
            if not self.model.is_header(r):
                return r
        return current

    def next_thread(self) -> None:
        """Select the next thread (skip headers)."""
        row = self.tree.currentIndex().row()
        next_r = self._next_row(row)
        if next_r != row:
            self.tree.setCurrentIndex(self.model.index(next_r, 0))

    def previous_thread(self) -> None:
        """Select the previous thread (skip headers)."""
        row = self.tree.currentIndex().row()
        prev_r = self._prev_row(row)
        if prev_r != row:
            self.tree.setCurrentIndex(self.model.index(prev_r, 0))

    def first_thread(self) -> None:
        """Select the first thread."""
        self._select_first_thread()

    def last_thread(self) -> None:
        """Select the last thread."""
        self._select_last_thread()

    def open_current_thread(self) -> None:
        """Open the currently selected thread."""
        thread_id = self.model.thread_id(self.tree.currentIndex())
        if thread_id:
            # Look up the section query this thread belongs to
            query = self._section_query_for_row(self.tree.currentIndex().row())
            self.app.open_thread(thread_id, query)

    def _section_query_for_row(self, row: int) -> str:
        """Return the query string for the section containing the given row."""
        for r in range(row, -1, -1):
            if self.model.is_header(r):
                _, data = self.model.rows[r]
                if isinstance(data, tuple) and len(data) >= 2:
                    return data[1].q
                break
        return self.queries[0][1] if self.queries else ''

    # -- MarkableActionsMixin hooks -------------------------------------
    # tag_thread, toggle_thread_tag, archive_thread, delete_thread, and
    # archive_to_local are provided by actions.MarkableActionsMixin.
    # "Marked" is dashboard-wide (not scoped to a single section).

    def _marked_query(self) -> str:
        return 'tag:marked'

    def _current_thread_id(self) -> Optional[str]:
        return self.model.thread_id(self.tree.currentIndex())

    def _advance_selection(self) -> None:
        """Advance the cursor before a destructive action and save the
        target row so the debounced refresh restores it."""
        self._advance_past_current()
        self._refresh_pending_row = self.tree.currentIndex().row()
