"""AppController — tab-title count batching (offscreen Qt).

Pins the behaviour the team learned the hard way: ``refresh_tab_titles``
must refresh all dirty search-tab thread counts with a *single*
``notmuch count --batch`` call, not one subprocess per tab on the UI
thread after every sync.
"""
import pytest

from lazarus import mainwindow
from lazarus.controller import AppController
from lazarus.search import SearchPanel
from tests.conftest import make_thread


@pytest.fixture
def mw(qapp, fake_app, notmuch_stub):
    win = mainwindow.MainWindow(fake_app)
    yield win


@pytest.fixture
def ctl(mw, fake_app):
    return AppController(fake_app, mw)  # type: ignore[arg-type]


def test_refresh_tab_titles_batches_one_count(ctl, mw, notmuch_stub, qapp):
    """All dirty search tabs are refreshed with a SINGLE count --batch."""
    notmuch_stub.threads = [
        make_thread('t1', 'Subject 1'),
        make_thread('t2', 'Subject 2'),
    ]
    for q in ('tag:inbox', 'tag:flagged'):
        ctl.open_search(q)

    dirty = [mw.tabs.widget(i) for i in range(mw.tabs.count())
             if isinstance(mw.tabs.widget(i), SearchPanel)]
    assert len(dirty) == 2
    for p in dirty:
        p._dirty_title = True

    notmuch_stub.count_calls.clear()
    ctl.refresh_tab_titles()

    # Exactly one batched invocation, never one-per-tab.
    batch_calls = [c for c in notmuch_stub.count_calls if c[0] == '__batch__']
    assert len(batch_calls) == 1
    per_tab = [c for c in notmuch_stub.count_calls if c[0] != '__batch__']
    assert per_tab == []
    # Results were applied back, clearing each panel's dirty title.
    assert all(not p.title_dirty for p in dirty)


def test_refresh_tab_titles_no_batch_when_none_dirty(ctl, mw, notmuch_stub, qapp):
    """With every search tab already clean, the refresh is a cheap no-op."""
    ctl.open_search('tag:inbox')
    # Force the panel deterministically clean (construction may have left
    # it dirty pending an async has_refreshed). No subprocess allowed.
    mw.tabs.widget(0)._dirty_title = False
    notmuch_stub.count_calls.clear()
    ctl.refresh_tab_titles()
    assert notmuch_stub.count_calls == []
