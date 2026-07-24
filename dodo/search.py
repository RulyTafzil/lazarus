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
from typing import Optional, Any, overload, Literal

from PyQt6.QtCore import Qt, QAbstractItemModel, QModelIndex, QObject, QSettings
from PyQt6.QtWidgets import QTreeView, QWidget, QAbstractSlider, QVBoxLayout, QLabel
from PyQt6.QtGui import QFont, QColor
import subprocess
import json
import logging

from . import app
from . import settings
from . import keymap
from . import panel
from . import actions

logger = logging.getLogger(__name__)

columns = ['date', '#', 'from', 'subject', 'tags']


def render_thread_cell(thread_d: dict, col: str, role: int,
                       hide_tags: set | None = None,
                       hide_query: str = '') -> Any:
    """Render a single cell for a thread in a search or dashboard view.

    Shared by :class:`SearchModel` and :class:`~dodo.dashboard.DashboardModel`
    to avoid duplicating the display logic for thread rows.

    :param thread_d: thread JSON dict from notmuch
    :param col: column name (``'date'``, ``'#'``, ``'from'``, ``'subject'``, ``'tags'``)
    :param role: Qt ``ItemDataRole``
    :param hide_tags: tags to suppress in the tag column (defaults to
                      ``settings.hide_tags``)
    :param hide_query: if non-empty, a ``'tag:X'``-style query whose tag
                       should be hidden (used by search panels)
    """
    if hide_tags is None:
        hide_tags = settings.hide_tags

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


class SearchModel(QAbstractItemModel):
    """A model containing the results of a search"""

    def __init__(self, q: str) -> None:
        super().__init__()
        self.q = q
        self.d = []
        self.json_str = ""
        self.num_threads = 0
        self.error_msg = None
        self.refresh()

    def refresh(self) -> None:
        """Refresh the model by (re-) running "notmuch search"."""
        logger.info("Beginning search refresh for '%s'", self.q)
        self.beginResetModel()
        try:
            r = subprocess.run(['notmuch', 'search', '--format=json', self.q],
                    capture_output=True, text=True, check=True)
            self.json_str = r.stdout
            self.d = json.loads(self.json_str)
            self.error_msg = None
        except subprocess.CalledProcessError as e:
            # We keep the previous data, just add an error message on top.
            self.error_msg = f"notmuch: {e.stderr}"
        self.threads = {thread['thread']: i for i,thread in enumerate(self.d)}
        self.num_threads = len(self.d)
        self.endResetModel()

    def refresh_thread(self, thread: QModelIndex|str):
        if isinstance(thread, str):
            thread_id = thread
            row = self.threads[thread]
        else:
            thread_id = self.thread_id(thread)
            row = thread.row()
            assert thread_id is not None

        logger.info("Search '%s': refreshing thread %s", self.q, thread_id)
        self.beginResetModel()
        try:
            r = subprocess.run(
                    ['notmuch', 'search', '--format=json', f'{self.q} AND thread:{thread_id}'],
                    capture_output=True,
                    text=True,
                    check=True,
                    )
            contents = json.loads(r.stdout)

            self.d[row:row+1] = contents
            self.threads = {thread['thread']: i for i,thread in enumerate(self.d)}
            self.num_threads = len(self.d)
        except subprocess.CalledProcessError as e:
            self.error_msg = f"notmuch: {e.stderr}"
        self.endResetModel()
        logger.info("Model refreshed for '%s'", self.q)

    def refresh_num_threads(self):
        """Only refresh the number of threads in the search, not the underlying data"""
        logger.info("Search '%s': Refreshing cached thread count", self.q)
        try:
            r = subprocess.run(
                    ['notmuch', 'count', '--output=threads', self.q],
                    capture_output=True,
                    text=True,
                    check=True,
                    )
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

        global columns
        if index.row() >= len(self.d) or index.column() >= len(columns):
            return None

        thread_d = self.d[index.row()]
        col = columns[index.column()]
        return render_thread_cell(thread_d, col, role, hide_query=self.q)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int=Qt.ItemDataRole.DisplayRole) -> Any:
        """Overrides `QAbstractItemModel.headerData` to populate a view with column names"""

        global columns
        if role == Qt.ItemDataRole.DisplayRole and section <= len(columns):
            return columns[section]
        else:
            return None

    def index(self, row: int, column: int, parent: QModelIndex=QModelIndex()) -> QModelIndex:
        """Construct a `QModelIndex` for the given row and column"""

        if not self.hasIndex(row, column, parent): return QModelIndex()
        else: return self.createIndex(row, column, None)

    def columnCount(self, index: QModelIndex=QModelIndex()) -> int:
        """The number of columns"""

        global columns
        return len(columns)

    def rowCount(self, index: QModelIndex=QModelIndex()) -> int:
        """The number of rows

        This is essentially an alias for :func:`num_threads`, but it also returns 0 if an index is
        given to tell Qt not to add any child items."""

        if not index or not index.isValid(): return self.num_threads
        else: return 0

    def parent(self, child: QModelIndex=None) -> Any:
        """Always return an invalid index, since there are no nested indices"""

        if not child: return super().parent()
        else: return QModelIndex()

class SearchPanel(panel.Panel):
    """A panel showing the results of a search

    This is used as the main entry point for the GUI, i.e. a search for "tag:inbox"."""

    def __init__(self, a: app.Dodo, q: str, keep_open: bool=False, parent: Optional[QWidget]=None):
        super().__init__(a, keep_open, parent)
        self.set_keymap(keymap.search_keymap)
        self.q = q
        self.conf = QSettings("dodo", "dodo")
        self.tree = QTreeView()
        self.error_view = QLabel()
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(f'QTreeView::item {{ padding: {settings.search_view_padding}px }}')
        self.model = SearchModel(q)
        self.tree.setModel(self.model)
        self.model.modelReset.connect(self.on_data_refresh)
        self.layout().addWidget(self.error_view)
        self.layout().addWidget(self.tree)
        self.tree.doubleClicked.connect(self.open_current_thread)
        self._setup_auto_open(self.tree)
        if self.tree.model().rowCount() > 0:
            self.tree.setCurrentIndex(self.tree.model().index(0,0))
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

    def on_data_refresh(self):
        if self.model.error_msg is None:
            self.error_view.hide()
        else:
            self.error_view.setText(self.model.error_msg)
            self.error_view.show()
        self.has_refreshed.emit()

    def before_close(self) -> bool:
        self.save_tree_geometry()
        return super().before_close()

    def restore_tree_geometry(self):
        tree_geometry = self.conf.value("search_tree_geometry")
        if tree_geometry:
            self.tree.header().restoreState(tree_geometry)

    def save_tree_geometry(self):
        self.conf.setValue("search_tree_geometry", self.tree.header().saveState())

    def snapshot_index(self) -> tuple[str|None, int]:
        current = self.tree.currentIndex()
        return self.model.thread_id(current), current.row()

    def restore_index(self, thread_id:str|None, fallback_row: int):
        thread_row = self.model.threads.get(thread_id, None)
        if thread_row is not None:
            index = self.model.index(thread_row, 0)
        else:
            index = self.model.index(max(0, min(self.model.num_threads-1, fallback_row)), 0)
        self.tree.setCurrentIndex(index)

    def refresh(self) -> None:
        """Refresh the search listing and restore the selection, if possible."""
        current_id, current_row = self.snapshot_index()
        self.model.refresh()
        self.restore_tree_geometry()
        self.restore_index(current_id, current_row)

        super().refresh()

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
            i = self.tree.model().index(row, 0)
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
            i = self.tree.model().index(row, 0)
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

        ix = self.model.index(self.tree.model().rowCount()-1, 0)
        if self.model.checkIndex(ix):
            self.tree.setCurrentIndex(ix)

    def prev_page(self) -> None:
        """Scroll up a page in the search"""
        bar = self.tree.verticalScrollBar()
        if bar.value() == bar.minimum():
            self.first_thread()
            return

        # Scroll up a page and keep selection at the bottommost thread
        bar.triggerAction(QAbstractSlider.SliderAction.SliderPageStepSub)
        pos = self.tree.rect().bottomLeft()
        self.tree.setCurrentIndex(self.tree.indexAt(pos))

    def next_page(self) -> None:
        """Scroll down a page in the search"""
        bar = self.tree.verticalScrollBar()
        if bar.value() == bar.maximum():
            self.last_thread()
            return

        # Scroll down a page and keep selection at the topmost thread
        bar.triggerAction(QAbstractSlider.SliderAction.SliderPageStepAdd)
        pos = self.tree.rect().topLeft()
        self.tree.setCurrentIndex(self.tree.indexAt(pos))

    def open_current_thread(self) -> None:
        """Open the selected thread"""

        thread_id = self.model.thread_id(self.tree.currentIndex())
        if thread_id:
            self.app.open_thread(thread_id, self.model.q)
    
    def toggle_thread_tag(self, tag: str) -> None:
        """Toggle the given thread tag"""

        thread = self.model.thread_json(self.tree.currentIndex())
        if thread:
            if tag in thread['tags']:
                tag_expr = '-' + tag
            else:
                tag_expr = '+' + tag
            self.tag_thread(tag_expr)


    def tag_thread(self, tag_expr: str, mode: Literal['tag', 'tag marked']='tag') -> None:
        """Apply the given tag expression to the selected thread

        A tag expression is a string consisting of one more statements of the form "+TAG"
        or "-TAG" to add or remove TAG, respectively, separated by whitespace."""

        if not ('+' in tag_expr or '-' in tag_expr):
            tag_expr = '+' + tag_expr
        
        if mode == 'tag':
            thread_id = self.model.thread_id(self.tree.currentIndex())
            if thread_id:
                subprocess.run(['notmuch', 'tag'] + tag_expr.split() + ['--', 'thread:' + thread_id])
                self.app.update_single_thread(thread_id)
        elif mode == 'tag marked':
            subprocess.run(['notmuch', 'tag'] + tag_expr.split() + ['-marked','--', f'tag:marked AND ({self.q})'])
            self.app.refresh_panels()

    def archive_thread(self) -> None:
        """Archive current thread, or all marked threads if any are marked."""
        if self._has_marked():
            subprocess.run(['notmuch', 'tag', '-inbox', '-unread', '-marked',
                           '--', f'tag:marked AND ({self.q})'])
            self.app.refresh_panels()
            self.app.status_message('Archived marked', 'info')
        else:
            thread = self.model.thread_json(self.tree.currentIndex())
            if not thread:
                return
            if actions.check_archive_refused(set(thread.get('tags', []))):
                self.app.status_message('Archive refused: thread has no tags beyond inbox/unread', 'warning')
                return
            thread_id = self.model.thread_id(self.tree.currentIndex())
            if thread_id:
                subprocess.run(['notmuch', 'tag', '-inbox', '-unread', '--', 'thread:' + thread_id])
                self.app.update_single_thread(thread_id)

    def delete_thread(self) -> None:
        """Delete current thread, or all marked threads if any are marked."""
        if self._has_marked():
            actions.move_to_trash(f'tag:marked AND ({self.q})')
            subprocess.run(['notmuch', 'tag', '-marked', '--',
                           f'tag:marked AND ({self.q})'])
            self.app.refresh_panels()
            self.app.status_message('Deleted marked', 'info')
        else:
            thread_id = self.model.thread_id(self.tree.currentIndex())
            if not thread_id:
                return
            actions.move_to_trash('thread:' + thread_id)
            self.app.update_single_thread(thread_id)
            self.app.status_message('Moved to trash', 'info')

    def archive_to_local(self) -> None:
        """Archive-to-local current thread, or all marked if any are marked."""
        if self._has_marked():
            actions.move_to_archive(f'tag:marked AND ({self.q})')
            subprocess.run(['notmuch', 'tag', '-marked', '--',
                           f'tag:marked AND ({self.q})'])
            self.app.refresh_panels()
            self.app.status_message('Archived marked to local', 'info')
        else:
            thread = self.model.thread_json(self.tree.currentIndex())
            if not thread:
                return
            if actions.check_archive_refused(set(thread.get('tags', []))):
                self.app.status_message('Archive refused: thread has no tags beyond inbox/unread', 'warning')
                return
            thread_id = self.model.thread_id(self.tree.currentIndex())
            if not thread_id:
                return
            actions.move_to_archive('thread:' + thread_id)
            self.app.update_single_thread(thread_id)
            self.app.status_message('Archived to local', 'info')

    def _has_marked(self) -> bool:
        """Check if any threads in the current view are marked."""
        r = subprocess.run(
            ['notmuch', 'count', '--output=threads',
             f'tag:marked AND ({self.q})'],
            capture_output=True, text=True)
        return r.returncode == 0 and int(r.stdout.strip() or '0') > 0

