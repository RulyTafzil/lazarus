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
"""Thread model, thread-item tree, and helpers.

Extracted from ``thread.py`` to keep that module focused on the
``ThreadPanel`` widget.
"""

from __future__ import annotations
from typing import List, Optional, Any, Literal, Iterable
from collections.abc import Generator

from PyQt6.QtCore import (
    QAbstractItemModel, QModelIndex, Qt, pyqtSignal,
)
import email.utils
import logging
import re

from . import settings
from . import style

logger = logging.getLogger(__name__)

RE_REGEX = re.compile(r'^R[Ee]: ')


# ---------------------------------------------------------------------------
# Thread-tree helpers
# ---------------------------------------------------------------------------

def iter_thread_messages(collection: list) -> Generator:
    """Recursively yield every message dict from a notmuch thread tree.

    The notmuch JSON format nests replies as ``[message, [child, ...]]``.
    This generator flattens that tree in depth-first order.
    """
    for elt in collection:
        if isinstance(elt, list):
            yield from iter_thread_messages(elt)
        else:
            yield elt


def flat_thread(d: list) -> List[dict]:
    """Return the thread as a flattened list of messages, sorted by date."""
    thread = list(iter_thread_messages(d))
    thread.sort(key=lambda m: m['timestamp'])
    return thread


def latest_message(thread_data: list) -> Optional[dict]:
    """Return the most recent message of a thread (``notmuch show`` shape).

    ``thread_data`` is one thread as returned by ``notmuch show --format=json``
    (a list of ``[message, [children]]`` trees). Returns None if empty.
    """
    msgs = flat_thread(thread_data)
    if not msgs:
        return None
    return max(msgs, key=lambda m: m['timestamp'])


def short_string(m: dict) -> str:
    """Return a short description string for the given message."""
    if 'headers' in m and 'From' in m['headers']:
        return m['headers']['From']
    return '(message)'


# ---------------------------------------------------------------------------
# ThreadItem -- one node in the thread tree
# ---------------------------------------------------------------------------

class ThreadItem:
    """A single message together with its reply-children."""

    def __init__(self, raw_data: list, parent: ThreadItem | None,
                 row_in_parent: int = 0):
        self.msg = raw_data[0]
        self.parent = parent
        # Index of this item within its parent's ``children`` (or within
        # the model's ``roots`` for top-level items) — lets
        # ``ThreadModel.parent`` resolve a parent index in O(1) instead
        # of scanning siblings on every call.
        self.row_in_parent = row_in_parent
        self.children = [ThreadItem(elt, self, i)
                         for i, elt in enumerate(raw_data[1])]

    def thread_string(self) -> str:
        from_hdr = self.msg.get('headers', {}).get('From', '(message) <>')
        name, addr = email.utils.parseaddr(from_hdr)
        if not name:
            name = addr if addr else from_hdr

        if not self.parent:
            return name

        subject = self.msg.get('headers', {}).get('Subject', '')
        while RE_REGEX.match(subject):
            subject = RE_REGEX.sub('', subject)
        prev_subject = self.parent.msg.get('headers', {}).get('Subject', '')
        while RE_REGEX.match(prev_subject):
            prev_subject = RE_REGEX.sub('', prev_subject)

        if subject != prev_subject:
            return f"{name} — {subject}"
        return name


def make_thread_trees(raw_thread_data: list) -> list[ThreadItem]:
    """Return the set of root ``ThreadItem`` nodes for a notmuch thread.

    If the thread is linear (every node has ≤1 child), all messages are
    returned as roots so the list view shows every message.
    """
    def _has_multiple_children(forest: list) -> bool:
        while forest:
            if len(forest) > 1:
                return True
            forest = forest[0][1]
        return False

    if _has_multiple_children(raw_thread_data):
        return [ThreadItem(root, None, i)
                for i, root in enumerate(raw_thread_data)]
    return [ThreadItem([msg, []], None, i)
            for i, msg in enumerate(iter_thread_messages(raw_thread_data))]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class EmptyThreadError(Exception):
    """Raised when ``notmuch show`` returns an empty result for a thread."""
    pass


# ---------------------------------------------------------------------------
# ThreadModel
# ---------------------------------------------------------------------------

class ThreadModel(QAbstractItemModel):
    """A model containing a thread, its messages, and metadata.

    :param thread_id: the unique thread identifier used by notmuch
    :param search_query: the notmuch query that led to this thread
    :param mode: initial list mode (``'conversation'`` or ``'thread'``)
    """

    matches: set[str]
    messageChanged = pyqtSignal(QModelIndex)

    def __init__(
        self, thread_id: str, search_query: str,
        mode: Literal['conversation', 'thread'],
    ) -> None:
        super().__init__()
        self.thread_id = thread_id
        self.query = search_query
        self.matches = set()
        self.raw_data: list = []
        self.roots: list[ThreadItem] = []
        self._mode: Literal['conversation', 'thread'] = mode
        self.error_msg: str | None = None

    # -- properties ---------------------------------------------------------

    @property
    def mode(self) -> Literal['conversation', 'thread']:
        return self._mode

    # -- mode toggling -------------------------------------------------------

    def toggle_mode(self) -> None:
        self.beginResetModel()
        self._mode = 'thread' if self._mode == 'conversation' else 'conversation'
        self.roots = self._compute_roots(self.raw_data)
        self.endResetModel()

    def _compute_roots(self, raw_data: list) -> list[ThreadItem]:
        if self._mode == 'conversation':
            return [ThreadItem([msg, []], None, i)
                    for i, msg in enumerate(flat_thread(raw_data))]
        return make_thread_trees(raw_data)

    # -- data fetching -------------------------------------------------------

    def _fetch_full_thread(self) -> list:
        """Fetch the thread tree from NED; raises on failure."""
        from .client import get_client
        res = get_client().get_thread(self.thread_id, full=False)
        tree = res.get('tree')
        if not isinstance(tree, list):
            raise RuntimeError(f'NED returned no thread tree for {self.thread_id}')
        return tree

    def _fetch_matching_ids(self) -> set[str]:
        """Fetch message IDs matching the thread + panel query from NED."""
        from .client import get_client
        clean_q = self.query.strip()
        q_expr = f'thread:{self.thread_id} AND ({clean_q})' if clean_q else f'thread:{self.thread_id}'
        ids = get_client().search_messages(q_expr)
        return set(ids)

    # -- navigation ----------------------------------------------------------

    def get_last_msg_idx(self,
                         parent: QModelIndex = QModelIndex()) -> QModelIndex:
        children = self._children_at(parent)
        if children:
            return self.get_last_msg_idx(
                self.index(len(children) - 1, 0, parent))
        return parent if parent.isValid() else QModelIndex()

    def default_message(self) -> QModelIndex:
        """Return the latest matching message or the last message."""
        matching_indices = [
            idx for idx in self.iterate_indices()
            if self.message_at(idx)['id'] in self.matches
        ]
        if matching_indices:
            return max(matching_indices, key=lambda idx: self.message_at(idx)['timestamp'])
        return self.get_last_msg_idx()

    def default_collapsed(self) -> set[str]:
        """Return IDs of branches with no matching messages."""
        irrelevant_branches: set[str] = set()

        def _prune(node: ThreadItem) -> bool:
            if node.msg['id'] in self.matches:
                return True
            has_relevant = any(_prune(c) for c in node.children)
            if not has_relevant:
                irrelevant_branches.add(node.msg['id'])
            return has_relevant

        for root in self.roots:
            _prune(root)
        return irrelevant_branches

    # -- refresh -------------------------------------------------------------

    def refresh(self) -> None:
        logger.info("Full thread refresh")
        try:
            matches = self._fetch_matching_ids()
            data = self._fetch_full_thread()
        except Exception as e:
            logger.exception("Refresh failed: %s", self.thread_id)
            self.error_msg = f"ned: {e}"
            return
        self.error_msg = None

        if not data:
            raise EmptyThreadError()

        assert len(data) == 1, data
        data = data[0]
        roots = self._compute_roots(data)
        self.beginResetModel()
        self.raw_data = data
        self.roots = roots
        self.matches = matches
        self.endResetModel()

    def refresh_message(self, msg_id: str) -> None:
        idx = self.find(msg_id)
        assert idx.isValid(), msg_id
        logger.info("Single message refresh: %s", msg_id)

        from .client import get_client
        try:
            matches = self._fetch_matching_ids()
            msg = get_client().get_message(msg_id)
        except Exception as e:
            logger.exception("Single refresh failed: %s", msg_id)
            self.error_msg = f"ned: {e}"
            return
        self.error_msg = None

        if not msg:
            logger.info("Message deleted, calling full refresh")
            self.refresh()
            return

        old_msg = self.message_at(idx)
        old_msg.clear()
        old_msg.update(msg)
        self.matches = matches
        self.dataChanged.emit(idx, idx)

    # -- tagging -------------------------------------------------------------

    def tag_message(self, idx: QModelIndex, tag_expr: str) -> None:
        if not idx.isValid():
            return
        m = self.message_at(idx)
        msg_id = m['id']
        if '+' not in tag_expr and '-' not in tag_expr:
            tag_expr = '+' + tag_expr
        from .client import get_client
        add_tags = [t[1:] for t in tag_expr.split() if t.startswith('+')]
        remove_tags = [t[1:] for t in tag_expr.split() if t.startswith('-')]
        get_client().modify_message_tags(msg_id, add=add_tags, remove=remove_tags)
        if 'tags' in m:
            tset = set(m['tags'])
            for t in tag_expr.split():
                if t.startswith('+'):
                    tset.add(t[1:])
                elif t.startswith('-'):
                    tset.discard(t[1:])
            m['tags'] = list(tset)
        self.messageChanged.emit(idx)

    def toggle_message_tag(self, idx: QModelIndex, tag: str) -> None:
        m = self.message_at(idx)
        tag_expr = ('-' + tag) if tag in m['tags'] else ('+' + tag)
        self.tag_message(idx, tag_expr)

    def mark_as_read(self, idx: QModelIndex) -> bool:
        m = self.message_at(idx)
        if 'unread' in m['tags']:
            self.tag_message(idx, '-unread')
            return True
        return False

    # -- message access ------------------------------------------------------

    def message_at(self, idx: QModelIndex) -> dict:
        assert idx.isValid(), idx
        return idx.internalPointer().msg

    def _children_at(self, idx: QModelIndex) -> list[ThreadItem]:
        if idx.isValid():
            return idx.internalPointer().children
        return self.roots

    def iterate_indices(self) -> Iterable[QModelIndex]:
        def _recurse(node: QModelIndex) -> Iterable[QModelIndex]:
            yield node
            for i, c in enumerate(self._children_at(node)):
                yield from _recurse(self.createIndex(i, 0, c))

        for i, r in enumerate(self.roots):
            yield from _recurse(self.createIndex(i, 0, r))

    def find(self, msg_id: str) -> QModelIndex:
        return next(
            (idx for idx in self.iterate_indices()
             if self.message_at(idx)['id'] == msg_id),
            QModelIndex(),
        )

    # -- QAbstractItemModel interface ----------------------------------------

    def data(self, index: QModelIndex,
             role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        item: ThreadItem = index.internalPointer()
        m = item.msg

        if role == Qt.ItemDataRole.DisplayRole:
            return item.thread_string()
        elif role == Qt.ItemDataRole.FontRole:
            return style.cell_font(
                settings.search_font, settings.search_font_size,
                bold='unread' in m.get('tags', []),
                italic=m['id'] not in self.matches)
        elif role == Qt.ItemDataRole.ForegroundRole:
            if m['id'] not in self.matches:
                return style.theme_color_or('fg_subject_irrelevant', 'fg')
            if 'unread' in m.get('tags', []):
                return style.theme_color_or('fg_subject_unread', 'fg')
            return style.theme_color('fg')
        return None

    def index(self, row: int, column: int,
              parent: QModelIndex = QModelIndex()) -> QModelIndex:
        children = self._children_at(parent)
        if row not in range(0, len(children)) or column != 0:
            return QModelIndex()
        return self.createIndex(row, column, children[row])

    def parent(self, child: QModelIndex = QModelIndex()) -> QModelIndex:  # type: ignore[override]
        # QObject.parent() (no args) isn't meaningful for a model; Qt only
        # calls parent(index) on the tree, so we don't implement it.
        data = child.internalPointer()
        if data is None or data.parent is None:
            return QModelIndex()
        # The parent's row within its own parent's children is stored on
        # the parent item — no sibling scan needed.
        return self.createIndex(data.parent.row_in_parent, 0, data.parent)

    def columnCount(self, index: QModelIndex = QModelIndex()) -> int:
        return 1

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._children_at(parent))
