"""Structural protocol checks — runtime_checkable isinstance narrowing."""
import pytest

from lazarus.protocols import ThreadList, ThreadView, LIST_METHODS, THREAD_METHODS
from lazarus.search import SearchPanel
from lazarus.tag import TagPanel


@pytest.fixture
def ctl(qapp, fake_app, notmuch_stub):
    from lazarus import mainwindow
    from lazarus.controller import AppController
    win = mainwindow.MainWindow(fake_app)
    win.resize(900, 600)
    win.show()
    return AppController(fake_app, win)  # type: ignore[arg-type]


def test_search_panel_satisfies_thread_list(ctl):
    sp = SearchPanel(ctl, 'tag:inbox')
    assert isinstance(sp, ThreadList)
    assert not isinstance(sp, ThreadView)


def test_tag_panel_does_not_satisfy_thread_list(ctl):
    tp = TagPanel(ctl)
    assert not isinstance(tp, ThreadList)


def test_allowlist_matches_keymap(ctl):
    """Every delegate_to_list/thread method string in the keymap is
    covered by the protocol allowlists (typos fail loudly, not silently)."""
    import re
    import lazarus.keymap as keymap
    for key, (_, fn) in keymap.global_keymap.items():
        src = str(fn)
        m = re.search(r"delegate_to_list\('([a-z_]+)'", src)
        if m:
            assert m.group(1) in LIST_METHODS, f'{key}: {m.group(1)}'
        m = re.search(r"delegate_to_thread\('([a-z_]+)'", src)
        if m:
            assert m.group(1) in THREAD_METHODS, f'{key}: {m.group(1)}'


def test_thread_list_methods_are_callable(ctl):
    sp = SearchPanel(ctl, 'tag:inbox')
    for name in ('next_thread', 'previous_thread', 'first_thread',
                 'last_thread', 'toggle_thread_tag', 'open_current_thread',
                 'prev_page', 'next_page', 'archive_thread', 'delete_thread',
                 'restore_thread_from_trash', 'archive_to_local'):
        assert callable(getattr(sp, name, None)), name
