"""Qt models — SearchModel, ThreadModel, TagModel (NED client stubbed)."""

from PyQt6.QtCore import Qt, QModelIndex

from lazarus.search import SearchModel
from lazarus.tag import TagModel
from lazarus.thread_model import ThreadModel, flat_thread, short_string
from tests.conftest import make_thread, make_message


# ---------------------------------------------------------------------------
# SearchModel
# ---------------------------------------------------------------------------

def test_search_model_loads_threads(client_stub, qapp):
    client_stub.threads = [make_thread('t1', 'Hello'), make_thread('t2', 'World')]
    model = SearchModel('tag:inbox')
    assert model.num_threads == 2
    assert model.thread_id(model.index(0, 0)) == 't1'


def test_search_model_filters_by_query(client_stub, qapp):
    client_stub.threads = [
        make_thread('t1', 'Hello', tags=['inbox']),
        make_thread('t2', 'Hi', tags=['unread']),
    ]
    model = SearchModel('tag:inbox')
    assert model.num_threads == 1


def test_refresh_thread_in_place_no_reset(client_stub, qapp):
    client_stub.threads = [make_thread('t1', 'Hello')]
    model = SearchModel('tag:inbox')
    reset = {'n': 0}
    changed = {'n': 0}
    model.modelReset.connect(lambda: reset.__setitem__('n', reset['n'] + 1))
    model.dataChanged.connect(lambda *_: changed.__setitem__('n', changed['n'] + 1))

    # mutate the row's subject via a re-query
    client_stub.threads[0]['subject'] = 'Changed'
    model.refresh_thread('t1')
    assert reset['n'] == 0
    assert changed['n'] == 1
    assert model.d[0]['subject'] == 'Changed'


def test_refresh_thread_noop_when_unchanged(client_stub, qapp):
    client_stub.threads = [make_thread('t1', 'Hello')]
    model = SearchModel('tag:inbox')
    reset = {'n': 0}
    changed = {'n': 0}
    model.modelReset.connect(lambda: reset.__setitem__('n', reset['n'] + 1))
    model.dataChanged.connect(lambda *_: changed.__setitem__('n', changed['n'] + 1))
    model.refresh_thread('t1')
    assert reset['n'] == 0 and changed['n'] == 0


def test_refresh_thread_row_removed_on_drop(client_stub, qapp):
    client_stub.threads = [make_thread('t1', 'Hello'), make_thread('t2', 'Bye')]
    model = SearchModel('tag:inbox')
    removed = []
    reset = {'n': 0}
    model.rowsRemoved.connect(lambda parent, first, last: removed.append((first, last)))
    model.modelReset.connect(lambda: reset.__setitem__('n', reset['n'] + 1))
    # t1 stops matching the query
    client_stub.threads = [make_thread('t2', 'Bye')]
    model.refresh_thread('t1')
    assert removed == [(0, 0)]
    assert reset['n'] == 0
    assert model.num_threads == 1


def test_search_model_error_keeps_stale_data(client_stub, qapp):
    client_stub.threads = [make_thread('t1', 'Hello')]
    model = SearchModel('tag:inbox')

    def boom(*a, **k):
        raise RuntimeError('NED unreachable')

    monkeypatch = __import__('pytest').MonkeyPatch.context()
    with monkeypatch:
        # Simulate daemon failure on refresh: stale data + error retained.
        client_stub.search = boom
        model.refresh()
    assert model.error_msg is not None
    assert model.num_threads == 1  # stale data retained


def test_render_thread_cell_roles(qapp):
    from lazarus.search import render_thread_cell
    t = make_thread('t1', 'Hello', tags=['inbox', 'unread'], authors='A B', total=3)
    assert render_thread_cell(t, 'subject', Qt.ItemDataRole.DisplayRole) == 'Hello'
    assert render_thread_cell(t, 'from', Qt.ItemDataRole.DisplayRole) == 'A B'
    assert render_thread_cell(t, 'date', Qt.ItemDataRole.DisplayRole) == '1 day ago'
    # tags column excludes hidden tags and renders icon/text
    from lazarus import settings
    settings.hide_tags = ['unread']
    tags_cell = render_thread_cell(t, 'tags', Qt.ItemDataRole.DisplayRole)
    assert 'unread' not in tags_cell
    # unread -> bold font
    font = render_thread_cell(t, 'subject', Qt.ItemDataRole.FontRole)
    assert font.bold()


# ---------------------------------------------------------------------------
# ThreadModel
# ---------------------------------------------------------------------------

def _thread_tree(client_stub, msg_ids=('m1', 'm2')):
    tree = [
        [make_message(msg_ids[0], 'First'), [
            [make_message(msg_ids[1], 'Reply'), []],
        ]],
    ]
    # notmuch show output shape: [ <list of roots> ]
    client_stub.thread_trees['thread:t1'] = [tree]
    client_stub.message_ids = list(msg_ids)


def _message_tree(msg, children=None):
    return [msg, children or []]


def test_thread_model_builds_tree(client_stub, qapp):
    thread = [_message_tree(
        make_message('m1', 'First'),
        [_message_tree(make_message('m2', 'Reply'))],
    )]
    _thread_tree(client_stub)
    model = ThreadModel('thread:t1', 'tag:inbox', 'thread')
    model.refresh()  # ThreadModel loads lazily via refresh(), like the panel
    assert model.rowCount() >= 1
    assert len(flat_thread(thread)) >= 1


def test_thread_model_parent_resolves_rows(client_stub, qapp):
    """parent() returns the parent index with the correct row (O(1) via
    ThreadItem.row_in_parent)."""
    thread = [_message_tree(
        make_message('m1', 'First'),
        [
            _message_tree(make_message('m2', 'Reply-1')),
            _message_tree(make_message('m3', 'Reply-2'),
                          [_message_tree(make_message('m4', 'Nested'))]),
        ],
    )]
    client_stub.thread_trees['thread:t1'] = [thread]
    client_stub.message_ids = ['m1', 'm2', 'm3', 'm4']
    model = ThreadModel('thread:t1', 'tag:inbox', 'thread')
    model.refresh()

    root = model.index(0, 0)
    assert model.parent(root) == QModelIndex()  # top-level item

    child0 = model.index(0, 0, root)
    child1 = model.index(1, 0, root)
    assert model.parent(child0) == root
    assert model.parent(child1) == root

    # m4 is nested under m3 (the second child) — its parent must resolve
    # to child1 with row 1, not a sibling scan artifact.
    nested = model.index(0, 0, child1)
    assert model.parent(nested) == child1
    assert nested.row() == 0


def test_flat_thread_sorts_by_timestamp(client_stub, qapp):
    m1 = make_message('m1', 'First', timestamp=100)
    m2 = make_message('m2', 'Second', timestamp=200)
    thread = [_message_tree(m2, [_message_tree(m1)])]
    client_stub.thread_trees['thread:t1'] = [thread]
    client_stub.message_ids = ['m1', 'm2']
    model = ThreadModel('thread:t1', 'tag:inbox', 'conversation')
    model.refresh()
    assert model.rowCount() >= 1
    flat = flat_thread(thread)
    assert [m['id'] for m in flat] == ['m1', 'm2']


def test_thread_model_toggle_message_tag(client_stub, qapp):
    """Message-level tag toggle targets id:<msgid> (not the whole thread)."""
    thread = [_message_tree(make_message('m1', 'First', tags=['unread']))]
    client_stub.thread_trees['thread:t1'] = [thread]
    client_stub.message_ids = ['m1']
    model = ThreadModel('thread:t1', 'tag:inbox', 'conversation')
    model.refresh()
    idx = model.index(0, 0)
    assert idx.isValid()

    # message is unread -> toggling removes unread from just that message
    model.toggle_message_tag(idx, 'unread')
    assert client_stub.modify_message_tags_calls[-1][0] == 'm1'
    assert client_stub.modify_message_tags_calls[-1][2] == ['unread']

    # and it reports the change on the message index
    changed = {'n': 0}
    model.messageChanged.connect(lambda _i: changed.__setitem__('n', changed['n'] + 1))
    model.toggle_message_tag(idx, 'flagged')
    assert client_stub.modify_message_tags_calls[-1][1] == ['flagged']
    assert changed['n'] == 1


def test_thread_model_toggle_mode(client_stub, qapp):
    thread = [_message_tree(
        make_message('m1', 'First'),
        [_message_tree(make_message('m2', 'Reply'))],
    )]
    client_stub.thread_trees['thread:t1'] = [thread]
    client_stub.message_ids = ['m1', 'm2']
    model = ThreadModel('thread:t1', 'tag:inbox', 'conversation')
    model.refresh()
    n0 = model.rowCount()
    model.toggle_mode()
    assert model.mode == 'thread'
    assert model.rowCount() >= 1
    model.toggle_mode()
    assert model.mode == 'conversation'
    assert model.rowCount() == n0


def test_short_string(client_stub, qapp):
    m = make_message('m1', 'First')
    assert 'alice@example.com' in short_string(m)


# ---------------------------------------------------------------------------
# TagModel
# ---------------------------------------------------------------------------

def test_tag_model_counts(client_stub, qapp):
    client_stub.threads = [
        make_thread('t1', 'A', tags=['inbox', 'unread']),
        make_thread('t2', 'B', tags=['inbox']),
        make_thread('t3', 'C', tags=['unread']),
    ]
    client_stub.tag_list = ['inbox', 'unread']
    model = TagModel()
    assert model.num_tags() == 2
    # column 0 = tag name; column 1 = '[unread/total]'
    inbox = [r for r in range(model.rowCount())
             if model.data(model.index(r, 0)) == 'inbox'][0]
    assert model.data(model.index(inbox, 1)) == '[1/2]'
    unread = [r for r in range(model.rowCount())
              if model.data(model.index(r, 0)) == 'unread'][0]
    assert model.data(model.index(unread, 1)) == '[2/2]'
