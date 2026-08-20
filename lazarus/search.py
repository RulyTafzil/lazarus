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
from typing import Optional, Any, Set, cast

from PyQt6.QtCore import Qt, QAbstractItemModel, QModelIndex, QRect, QSize, QSettings
from PyQt6.QtWidgets import (
    QWidget, QLabel, QStyledItemDelegate, QStyle, QStyleOptionViewItem,
    QHeaderView,
)
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen
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

# Values for ``settings.search_list_mode``.
LIST_MODE_FLAT = 'list'
LIST_MODE_CARD = 'card'

# NerdFont glyph prefix for a thread's message count (also used by
# ``render_thread_cell`` for the '#' column).
COUNT_GLYPH = '\uf086'

# Default column widths (px) used to reset a flat list when a legacy card
# layout is found in the shared ``search_tree_geometry`` key.  Subject
# (col 3) is the flexible one — stretchLastSection fills it out.
_FLAT_WIDTHS = {'date': 90, '#': 44, 'from': 220, 'subject': 520, 'tags': 170}


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


class CardDelegate(QStyledItemDelegate):
    """Two-line 'card' renderer for the search list.

    Each thread row is drawn as a single flowing card spanning the full
    row width (the rigid date/#/from/subject/tags column grid is hidden
    in card mode):

      line 1:  [thread-count] From ....................... Date
      line 2:                  Subject .................. [tags]

    The card has a light rounded border so adjacent threads read as
    distinct groups.  Text colours/fonts come from the same helper the
    flat view uses (:func:`render_thread_cell`), so unread/flagged
    boldness, ``search_color_overrides`` and per-column theme colours
    stay in parity regardless of mode.

    Uses a single full-width column so From/Subject are only elided when
    they genuinely run out of room (a right-field reserve keeps the date
    and tags from overlapping) — no premature '...' truncation.
    """

    margin_h = 6      # gap from the panel edge to the card border
    pad_h = 8         # inner horizontal padding
    pad_v = 5         # inner vertical padding
    line_gap = 2      # vertical gap between the two text lines
    col_gap = 10      # horizontal gap between left and right text
    radius = 6        # rounded-corner radius
    border_alpha = 70 # border opacity for unselected cards

    def sizeHint(self, option: QStyleOptionViewItem,
                 index: QModelIndex) -> QSize:  # type: ignore[override]
        fm = QFontMetrics(style.cell_font(
            settings.search_font, settings.search_font_size))
        # two lines + vertical padding + 1px border each side
        h = (2 * self.pad_v + 2 * fm.height() + self.line_gap + 2)
        return QSize(max(option.rect.width(), 60), h)

    def paint(self, painter: Optional[QPainter],
              option: QStyleOptionViewItem,
              index: QModelIndex) -> None:  # type: ignore[override]
        if painter is None:
            super().paint(painter, option, index)
            return
        model = index.model()
        if model is None:
            super().paint(painter, option, index)
            return
        thread = cast("SearchModel", model).thread_json(index)
        if not isinstance(thread, dict):
            super().paint(painter, option, index)
            return
        hide_query: str = getattr(model, 'q', '') or ''

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        # ── card geometry ────────────────────────────────────────────
        rect = option.rect
        if rect.width() <= 2 * self.margin_h:
            super().paint(painter, option, index)
            return
        outer = rect.adjusted(self.margin_h, 1, -self.margin_h, -1)
        card = outer.adjusted(1, 1, -1, -1)  # keep border inside the rect
        inner = card.adjusted(
            self.pad_h, self.pad_v, -self.pad_h, -self.pad_v)

        fill = (style.theme_color_or('bg_highlight', 'bg')
                if selected else style.theme_color_or('bg', 'bg'))
        border = QColor(style.theme_color_or('fg_dim', 'fg'))
        border.setAlpha(120 if selected else self.border_alpha)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(fill)
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(card, self.radius, self.radius)

        # ── line 1: [indicator] From ........ Date ──────────────────
        from_text = render_thread_cell(thread, 'from',
                                       Qt.ItemDataRole.DisplayRole, hide_query=hide_query)
        from_col = render_thread_cell(thread, 'from',
                                      Qt.ItemDataRole.ForegroundRole, hide_query=hide_query)
        from_font = render_thread_cell(thread, 'from',
                                       Qt.ItemDataRole.FontRole, hide_query=hide_query)
        date_text = render_thread_cell(thread, 'date',
                                       Qt.ItemDataRole.DisplayRole, hide_query=hide_query)
        date_col = render_thread_cell(thread, 'date',
                                      Qt.ItemDataRole.ForegroundRole, hide_query=hide_query)
        date_font = render_thread_cell(thread, 'date',
                                       Qt.ItemDataRole.FontRole, hide_query=hide_query)
        ind = render_thread_cell(thread, '#',
                                 Qt.ItemDataRole.DisplayRole, hide_query=hide_query)

        fm_from = QFontMetrics(from_font)
        fm_date = QFontMetrics(date_font)
        line_h = max(fm_from.height(), fm_date.height())
        y1 = inner.top()

        # Reserve a constant width for the thread-count indicator so the
        # sender (and subject) start at the same x on every card whether
        # the message is part of a thread or not.  The count is drawn
        # right-aligned inside its box, so larger counts grow leftward
        # without shifting the sender.
        ind_reserved = fm_from.horizontalAdvance(
            f'{COUNT_GLYPH} 00')
        left = inner.left() + ind_reserved + self.col_gap
        right_reserve = fm_date.horizontalAdvance(date_text) + self.col_gap
        avail_from = max(0, inner.width() - (left - inner.left())
                         - right_reserve)
        from_elided = fm_from.elidedText(
            from_text, Qt.TextElideMode.ElideRight,
            avail_from if avail_from > 0 else 1)

        if ind:
            painter.setFont(from_font)
            painter.setPen(from_col)
            painter.drawText(QRect(inner.left(), y1, ind_reserved, line_h),
                             Qt.AlignmentFlag.AlignRight
                             | Qt.AlignmentFlag.AlignVCenter, ind)
        painter.setFont(from_font)
        painter.setPen(from_col)
        painter.drawText(QRect(left, y1, avail_from, line_h),
                         Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignVCenter, from_elided)
        painter.setFont(date_font)
        painter.setPen(date_col)
        painter.drawText(QRect(inner.left(), y1, inner.width(), line_h),
                         Qt.AlignmentFlag.AlignRight
                         | Qt.AlignmentFlag.AlignVCenter, date_text)

        # ── line 2: Subject ........ [tags] ────────────────────────
        subj_text = render_thread_cell(thread, 'subject',
                                       Qt.ItemDataRole.DisplayRole, hide_query=hide_query)
        subj_col = render_thread_cell(thread, 'subject',
                                      Qt.ItemDataRole.ForegroundRole, hide_query=hide_query)
        subj_font = render_thread_cell(thread, 'subject',
                                       Qt.ItemDataRole.FontRole, hide_query=hide_query)
        tags_text = render_thread_cell(thread, 'tags',
                                       Qt.ItemDataRole.DisplayRole, hide_query=hide_query)
        tags_col = render_thread_cell(thread, 'tags',
                                      Qt.ItemDataRole.ForegroundRole, hide_query=hide_query)
        tags_font = render_thread_cell(thread, 'tags',
                                       Qt.ItemDataRole.FontRole, hide_query=hide_query)

        fm_subj = QFontMetrics(subj_font)
        fm_tags = QFontMetrics(tags_font)
        y2 = inner.top() + line_h + self.line_gap
        right_reserve = fm_tags.horizontalAdvance(tags_text) + self.col_gap
        avail_subj = max(0, inner.width() - (left - inner.left())
                         - right_reserve)
        subj_elided = fm_subj.elidedText(
            subj_text, Qt.TextElideMode.ElideRight,
            avail_subj if avail_subj > 0 else 1)

        painter.setFont(subj_font)
        painter.setPen(subj_col)
        painter.drawText(QRect(left, y2, avail_subj, line_h),
                         Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignVCenter, subj_elided)
        painter.setFont(tags_font)
        painter.setPen(tags_col)
        painter.drawText(QRect(inner.left(), y2, inner.width(), line_h),
                         Qt.AlignmentFlag.AlignRight
                         | Qt.AlignmentFlag.AlignVCenter, tags_text)

        painter.restore()


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
        # List-mode rendering: 'list' = flat table (default delegate,
        # restored header); 'card' = two-line card (CardDelegate, header
        # hidden, single full-width column).  Config-only: decided by
        # ``settings.search_list_mode``, not toggleable in-app.
        self._default_delegate = QStyledItemDelegate(self.tree)
        self._card_delegate = CardDelegate(self.tree)
        self._apply_geometry_and_mode()
        if self.model.rowCount() > 0:
            self.tree.setCurrentIndex(self.model.index(0, 0))
        self.on_data_refresh()

    # -- search list mode (config-only) ---------------------------------

    def _apply_geometry_and_mode(self) -> None:
        """Configure column layout for the current list mode.

        Card mode is a single stretched column, so its column widths are
        meaningless and must NOT be restored from (or saved to) the shared
        ``search_tree_geometry`` key — a card panel once persisted a huge
        date-column geometry there, which later flat-mode panels restored,
        leaving only the date column filled.  Only flat mode restores/saves
        the classic column widths.  If an older build already left a card
        layout (grid hidden) in the key, replace it with sane flat widths.
        """
        if settings.search_list_mode == LIST_MODE_CARD:
            self._apply_list_mode()
            return
        self.restore_tree_geometry()
        # Detect a legacy card leftover: a card layout hides the grid (cols
        # 1-4); flat shows it.  If the restored geometry came back with the
        # grid hidden, it's card poison — restore is meaningless and wrong
        # for flat, so use sane flat widths and purge the entry.
        header = self.tree.header()
        legacy_card = header is not None and any(
            header.isSectionHidden(c) for c in range(1, len(columns)))
        self._apply_list_mode()
        if legacy_card and header is not None:
            QSettings('lazarus', 'lazarus').remove(self._geometry_key)
            for c, name in enumerate(columns):
                header.resizeSection(c, _FLAT_WIDTHS.get(name, 100))

    def save_tree_geometry(self) -> None:
        """Only flat mode persists column widths; card mode never writes
        the shared key (its single stretched column has no meaningful widths
        to remember, and saving it would poison later flat modes)."""
        if settings.search_list_mode == LIST_MODE_CARD:
            return
        super().save_tree_geometry()

    def _apply_list_mode(self) -> None:
        """Reconfigure the tree view for the current list mode."""
        tree = self.tree
        header = tree.header()
        if header is None:
            return
        if settings.search_list_mode == LIST_MODE_CARD:
            tree.setItemDelegate(self._card_delegate)
            tree.setHeaderHidden(True)
            for c in range(1, len(columns)):
                tree.setColumnHidden(c, True)
            header.setSectionHidden(0, False)
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setStretchLastSection(False)
            tree.setUniformRowHeights(True)
        else:
            tree.setItemDelegate(self._default_delegate)
            tree.setHeaderHidden(False)
            for c in range(1, len(columns)):
                tree.setColumnHidden(c, False)
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            header.setStretchLastSection(True)
            tree.setUniformRowHeights(True)
        logger.debug('[list-mode] applied mode=%r rows=%d uniform=%s',
                     settings.search_list_mode, self.model.rowCount(),
                     tree.uniformRowHeights())

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
        self._apply_geometry_and_mode()
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

    @property
    def title_dirty(self) -> bool:
        """True when the tab title's thread count needs a refresh."""
        return self._dirty_title

    def apply_thread_count(self, count: int) -> None:
        """Store a thread count fetched in batch.

        The controller collects every dirty search panel's query and
        runs a single ``notmuch count --batch`` for all of them (see
        ``AppController.refresh_tab_titles``); each panel receives its
        result here instead of spawning its own subprocess.
        """
        self.model.num_threads = count
        self._dirty_title = False

    def title(self) -> str:
        """Use the configured tab title.

        Pure formatting — the thread count is refreshed by the
        controller (``refresh_tab_titles`` → ``apply_thread_count``).
        """
        logger.info("Search '%s': updating title", self.q)
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
        """Fetch the most recent message of the selected thread.

        Must request the same parts the thread preview shows
        (``ThreadModel._fetch_full_thread``): notmuch ``show`` elides
        ``text/html`` parts unless ``--include-html`` is passed, so a
        reply/forward from the list to an HTML-only email would otherwise
        come back with an empty body.  ``--decrypt=true`` keeps encrypted
        content in parity with the preview too.
        """
        thread_id = self._current_thread_id()
        if not thread_id:
            return None
        try:
            r = notmuch.run(
                'show', '--exclude=false', '--format=json',
                '--include-html', '--decrypt=true',
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

