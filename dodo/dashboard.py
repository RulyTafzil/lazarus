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
from typing import Optional, Any, List, Tuple, Literal

from PyQt6.QtCore import Qt, QAbstractItemModel, QModelIndex, QSettings
from PyQt6.QtWidgets import QTreeView, QHeaderView
from PyQt6.QtGui import QFont, QColor

import logging

from . import app
from . import settings
from . import keymap
from . import panel
from .search import SearchModel, columns

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
        # Refresh each section's underlying SearchModel
        for _, _, model in self.sections:
            model.refresh()
        # Rebuild the flat row list
        new_rows: List[Tuple[str, Any]] = []
        for label, query_str, model in self.sections:
            new_rows.append(('header', (label, model)))
            items = model.d[:self.max_items] if self.max_items else model.d
            for thread in items:
                new_rows.append(('thread', thread))
        self.rows = new_rows
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

    def _rebuild_from_sections(self) -> None:
        """Rebuild rows from section models without re-querying."""
        self.beginResetModel()
        new_rows: List[Tuple[str, Any]] = []
        for label, _, model in self.sections:
            new_rows.append(('header', (label, model)))
            items = model.d[:self.max_items] if self.max_items else model.d
            for thread in items:
                new_rows.append(('thread', thread))
        self.rows = new_rows
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
            thread_d = data
            col = columns[col_idx]

            if role == Qt.ItemDataRole.DisplayRole:
                if col == 'date':
                    return thread_d['date_relative']
                elif col == 'from':
                    return thread_d['authors']
                elif col == 'subject':
                    return thread_d['subject']
                elif col == 'tags':
                    tag_icons = []
                    for t in thread_d['tags']:
                        if t not in settings.hide_tags:
                            tag_icons.append(settings.tag_icons[t] if t in settings.tag_icons else f'[{t}]')
                    return ' '.join(tag_icons)
                elif col == '#':
                    total = thread_d.get('total', 1)
                    return f'\uf086 {total}' if total > 1 else ''
            elif role == Qt.ItemDataRole.FontRole:
                if col == 'tags':
                    font = QFont(settings.tag_font, settings.tag_font_size)
                else:
                    font = QFont(settings.search_font, settings.search_font_size)
                if 'unread' in thread_d['tags'] or 'flagged' in thread_d['tags']:
                    font.setBold(True)
                return font
            elif role == Qt.ItemDataRole.ForegroundRole:
                for tag in settings.search_color_overrides.keys() & thread_d['tags']:
                    if col in settings.search_color_overrides[tag]:
                        return QColor(settings.search_color_overrides[tag][col])
                color = 'fg_' + col
                unread_color = 'fg_' + col + '_unread'
                flagged_color = 'fg_' + col + '_flagged'
                if 'unread' in thread_d['tags'] and unread_color in settings.theme:
                    return QColor(settings.theme[unread_color])
                elif 'flagged' in thread_d['tags'] and flagged_color in settings.theme:
                    return QColor(settings.theme[flagged_color])
                elif color in settings.theme:
                    return QColor(settings.theme[color])
                else:
                    return QColor(settings.theme['fg'])
            elif role == Qt.ItemDataRole.ToolTipRole and col == 'tags':
                return ' '.join(thread_d['tags'])

        return None

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


class DashboardPanel(panel.Panel):
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

        # Span header rows across all columns
        self.tree.model().modelReset.connect(self._span_headers)

        # Restore column widths from previous session
        self.conf = QSettings("dodo", "dodo")
        self.restore_tree_geometry()

        self.layout().addWidget(self.tree)

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
        if self.hasFocus():
            self.refresh()
        else:
            self.dirty = True

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
        for row in range(self.model.rowCount() - 1, -1, -1):
            if not self.model.is_header(row):
                self.tree.setCurrentIndex(self.model.index(row, 0))
                break

    def open_current_thread(self) -> None:
        """Open the currently selected thread."""
        thread_id = self.model.thread_id(self.tree.currentIndex())
        if thread_id:
            # Find which section's query to use
            # Use the first section query as context; not ideal but functional
            query = self.queries[0][1] if self.queries else ''
            self.app.open_thread(thread_id, query)

    def tag_thread(self, tag_expr: str, mode: Literal['tag', 'tag marked'] = 'tag') -> None:
        """Apply a tag expression to the current thread or all marked threads."""
        import subprocess

        if not ('+' in tag_expr or '-' in tag_expr):
            tag_expr = '+' + tag_expr

        if mode == 'tag marked':
            # Tag all marked threads across all dashboard sections
            subprocess.run(['notmuch', 'tag'] + tag_expr.split() + ['-marked', '--', 'tag:marked'])
            self.app.refresh_panels()
        else:
            thread_id = self.model.thread_id(self.tree.currentIndex())
            if not thread_id:
                return
            subprocess.run(['notmuch', 'tag'] + tag_expr.split() + ['--', 'thread:' + thread_id])
            self.app.update_single_thread(thread_id)

    def archive_thread(self) -> None:
        """Archive the current thread, but only if it has tags beyond inbox/unread."""
        import subprocess
        thread_id = self.model.thread_id(self.tree.currentIndex())
        if not thread_id:
            return
        row = self.tree.currentIndex().row()
        typ, data = self.model.rows[row]
        if typ != 'thread':
            return
        other_tags = set(data.get('tags', [])) - {'inbox', 'unread'}
        if not other_tags:
            self.app.status_message('Archive refused: thread has no tags beyond inbox/unread', 'warning')
            return  # refuse: thread has no categorizing tags
        subprocess.run(['notmuch', 'tag', '-inbox', '-unread', '--', 'thread:' + thread_id])
        self.app.update_single_thread(thread_id)

    def delete_thread(self) -> None:
        """Move the current thread to Trash: tag +deleted and move files to Trash folder."""
        import subprocess
        import os
        import re
        thread_id = self.model.thread_id(self.tree.currentIndex())
        if not thread_id:
            return
        # Tag in notmuch
        subprocess.run(['notmuch', 'tag', '+deleted', '-inbox', '-unread', '--', 'thread:' + thread_id])
        # Move files to Trash folder (Thunderbird/neomutt approach)
        r = subprocess.run(['notmuch', 'search', '--exclude=false', '--output=files', '--', 'thread:' + thread_id],
                          capture_output=True, text=True)
        for f in r.stdout.strip().split('\n'):
            if not f:
                continue
            # Determine Trash path from the file's current location
            parts = f.split('/Mail/', 1)
            if len(parts) != 2:
                continue
            account, rest = parts[1].split('/', 1)
            folder = rest.split('/', 1)[0] if '/' in rest else rest
            # Gmail uses [Gmail]/Trash, standard IMAP uses Trash
            trash_dir = os.path.join('/home/rulyt/Mail', account, '[Gmail]', 'Trash', 'cur')
            if not os.path.isdir(trash_dir):
                trash_dir = os.path.join('/home/rulyt/Mail', account, 'Trash', 'cur')
            if not os.path.isdir(trash_dir):
                os.makedirs(trash_dir, exist_ok=True)
            basename = os.path.basename(f)
            # Strip mbsync UID annotation to avoid duplicate UID errors
            basename = re.sub(r',U=\d+', '', basename)
            dest = os.path.join(trash_dir, basename)
            try:
                os.rename(f, dest)
            except OSError as e:
                logger.warning('trash move failed: %s', e)
        self.app.update_single_thread(thread_id)
        self.app.status_message('Moved to trash', 'info')

    def toggle_thread_tag(self, tag: str) -> None:
        """Toggle the given tag on the current thread."""
        import subprocess
        thread_id = self.model.thread_id(self.tree.currentIndex())
        if not thread_id:
            return
        row = self.tree.currentIndex().row()
        typ, data = self.model.rows[row]
        if typ != 'thread':
            return
        thread_d = data
        if tag in thread_d.get('tags', []):
            tag_expr = '-' + tag
        else:
            tag_expr = '+' + tag
        if not ('+' in tag_expr or '-' in tag_expr):
            tag_expr = '+' + tag_expr
        subprocess.run(['notmuch', 'tag'] + tag_expr.split() + ['--', 'thread:' + thread_id])
        self.app.update_single_thread(thread_id)
