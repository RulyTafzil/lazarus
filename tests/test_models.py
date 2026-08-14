"""Qt models — SearchModel, ThreadModel, TagModel (notmuch stubbed)."""
import json

from PyQt6.QtCore import Qt, QModelIndex

from lazarus.search import SearchModel
from lazarus.tag import TagModel
from lazarus.thread_model import ThreadModel, flat_thread, short_string
from tests.conftest import make_thread, make_message


# ---------------------------------------------------------------------------
# SearchModel
# ---------------------------------------------------------------------------

def test_search_model_loads_threads(notmuch_stub, qapp):
    notmuch_stub.threads = [make_thread('t1', 'Hello'), make_thread('t2', 'World')]
    model = SearchModel('tag:inbox')
    assert model.num_threads == 2
    assert model.thread_id(model.index(0, 0)) == 't1'


def test_search_model_filters_by_query(notmuch_stub, qapp):
    notmuch_stub.threads = [
        make_thread('t1', 'Hello', tags=['inbox']),
        make_thread('t2', 'Hi', tags=['unread']),
    ]
    model = SearchModel('tag:inbox')
    assert model.num_threads == 1


def test_refresh_thread_in_place_no_reset(notmuch_stub, qapp):
    notmuch_stub.threads = [make_thread('t1', 'Hello')]
    model = SearchModel('tag:inbox')
    reset = {'n': 0}
    changed = {'n': 0}
    model.modelReset.connect(lambda: reset.__setitem__('n', reset['n'] + 1))
    model.dataChanged.connect(lambda *_: changed.__setitem__('n', changed['n'] + 1))

    # mutate the row's subject via a re-query
    notmuch_stub.threads[0]['subject'] = 'Changed'
    model.refresh_thread('t1')
    assert reset['n'] == 0
    assert changed['n'] == 1
    assert model.d[0]['subject'] == 'Changed'


def test_refresh_thread_noop_when_unchanged(notmuch_stub, qapp):
    notmuch_stub.threads = [make_thread('t1', 'Hello')]
    model = SearchModel('tag:inbox')
    reset = {'n': 0}
    changed = {'n': 0}
    model.modelReset.connect(lambda: reset.__setitem__('n', reset['n'] + 1))
    model.dataChanged.connect(lambda *_: changed.__setitem__('n', changed['n'] + 1))
    model.refresh_thread('t1')
    assert reset['n'] == 0 and changed['n'] == 0


def test_refresh_thread_full_reset_on_drop(notmuch_stub, qapp):
    notmuch_stub.threads = [make_thread('t1', 'Hello'), make_thread('t2', 'Bye')]
    model = SearchModel('tag:inbox')
    reset = {'n': 0}
    model.modelReset.connect(lambda: reset.__setitem__('n', reset['n'] + 1))
    # t1 stops matching the query
    notmuch_stub.threads = [make_thread('t2', 'Bye')]
    model.refresh_thread('t1')
    assert reset['n'] == 1
    assert model.num_threads == 1


def test_search_model_error_keeps_stale_data(notmuch_stub, qapp):
    notmuch_stub.threads = [make_thread('t1', 'Hello')]
    model = SearchModel('tag:inbox')

    def boom(*a, **k):
        import subprocess
        raise subprocess.CalledProcessError(1, 'notmuch', stderr='db locked')

    import lazarus.notmuch as nm
    import pytest
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nm, 'search_json', boom)
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

def _thread_tree(notmuch_stub, msg_ids=('m1', 'm2')):
    tree = [
        [make_message(msg_ids[0], 'First'), [
            [make_message(msg_ids[1], 'Reply'), []],
        ]],
    ]
    notmuch_stub.threads = tree  # search_json returns the tree directly
    # ThreadModel uses a different notmuch call — patch per-model below


def _patch_thread_notmuch(monkeypatch, thread, msg_ids):
    """ThreadModel calls notmuch.run directly.

    Real ``notmuch show`` output: ``[[thread]]`` where each thread is a
    list of ``[message, [children]]`` trees.
    """
    import subprocess
    import lazarus.notmuch as nm

    def fake_run(*args, **kwargs):
        if args and args[0] == 'show':
            out = json.dumps([thread])      # list of threads
        else:  # 'search' --output=messages
            out = json.dumps(list(msg_ids))
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr='')

    monkeypatch.setattr(nm, 'run', fake_run)


def _message_tree(msg, children=None):
    return [msg, children or []]


def test_thread_model_builds_tree(notmuch_stub, qapp, monkeypatch):
    thread = [_message_tree(
        make_message('m1', 'First'),
        [_message_tree(make_message('m2', 'Reply'))],
    )]
    _patch_thread_notmuch(monkeypatch, thread, ('m1', 'm2'))
    model = ThreadModel('thread:t1', 'tag:inbox', 'thread')
    model.refresh()  # ThreadModel loads lazily via refresh(), like the panel
    assert model.rowCount() >= 1
    assert len(flat_thread(thread)) >= 1


def test_flat_thread_sorts_by_timestamp(notmuch_stub, qapp, monkeypatch):
    m1 = make_message('m1', 'First', timestamp=100)
    m2 = make_message('m2', 'Second', timestamp=200)
    thread = [_message_tree(m2, [_message_tree(m1)])]
    _patch_thread_notmuch(monkeypatch, thread, ('m1', 'm2'))
    model = ThreadModel('thread:t1', 'tag:inbox', 'conversation')
    model.refresh()
    assert model.rowCount() >= 1
    flat = flat_thread(thread)
    assert [m['id'] for m in flat] == ['m1', 'm2']


def test_thread_model_toggle_message_tag(notmuch_stub, qapp, monkeypatch):
    """Message-level tag toggle targets id:<msgid> (not the whole thread)."""
    import subprocess
    import lazarus.notmuch as nm
    thread = [_message_tree(make_message('m1', 'First', tags=['unread']))]

    def fake_run(*args, **kwargs):
        if args and args[0] == 'show':
            out = json.dumps([thread])
        else:
            out = json.dumps(['m1'])
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr='')

    monkeypatch.setattr(nm, 'run', fake_run)
    model = ThreadModel('thread:t1', 'tag:inbox', 'conversation')
    model.refresh()
    idx = model.index(0, 0)
    assert idx.isValid()

    # message is unread -> toggling removes unread from just that message
    model.toggle_message_tag(idx, 'unread')
    assert notmuch_stub.tag_calls[-1][0] == '-unread'
    assert notmuch_stub.tag_calls[-1][1].startswith('id:m1')

    # and it reports the change on the message index
    changed = {'n': 0}
    model.messageChanged.connect(lambda _i: changed.__setitem__('n', changed['n'] + 1))
    model.toggle_message_tag(idx, 'flagged')
    assert notmuch_stub.tag_calls[-1][0] == '+flagged'
    assert changed['n'] == 1


def test_thread_model_toggle_mode(notmuch_stub, qapp, monkeypatch):
    thread = [_message_tree(
        make_message('m1', 'First'),
        [_message_tree(make_message('m2', 'Reply'))],
    )]
    _patch_thread_notmuch(monkeypatch, thread, ('m1', 'm2'))
    model = ThreadModel('thread:t1', 'tag:inbox', 'conversation')
    model.refresh()
    n0 = model.rowCount()
    model.toggle_mode()
    assert model.mode == 'thread'
    assert model.rowCount() >= 1
    model.toggle_mode()
    assert model.mode == 'conversation'
    assert model.rowCount() == n0


def test_short_string(notmuch_stub, qapp):
    m = make_message('m1', 'First')
    assert 'alice@example.com' in short_string(m)


# ---------------------------------------------------------------------------
# TagModel
# ---------------------------------------------------------------------------

def test_tag_model_counts(notmuch_stub, qapp):
    notmuch_stub.threads = [
        make_thread('t1', 'A', tags=['inbox', 'unread']),
        make_thread('t2', 'B', tags=['inbox']),
        make_thread('t3', 'C', tags=['unread']),
    ]
    notmuch_stub.tag_list = ['inbox', 'unread']
    model = TagModel()
    assert model.num_tags() == 2
    # column 0 = tag name; column 1 = '[unread/total]'
    inbox = [r for r in range(model.rowCount())
             if model.data(model.index(r, 0)) == 'inbox'][0]
    assert model.data(model.index(inbox, 1)) == '[1/2]'
    unread = [r for r in range(model.rowCount())
              if model.data(model.index(r, 0)) == 'unread'][0]
    assert model.data(model.index(unread, 1)) == '[2/2]'
