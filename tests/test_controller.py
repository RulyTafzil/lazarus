"""AppController — panel orchestration, dispatch, tag bar (offscreen Qt)."""
import pytest

from PyQt6.QtCore import Qt

from lazarus import mainwindow
from lazarus.controller import AppController
from lazarus.panel import Panel
from lazarus.search import SearchPanel
from tests.conftest import make_thread


@pytest.fixture
def mw(qapp, fake_app, notmuch_stub):
    win = mainwindow.MainWindow(fake_app)
    win.resize(1000, 700)
    win.show()
    return win


@pytest.fixture
def ctl(mw, fake_app):
    return AppController(fake_app, mw)  # type: ignore[arg-type]


class DummyPanel(Panel):
    def __init__(self, ctl, name):
        super().__init__(ctl)
        self._name = name

    def title(self):
        return self._name


def test_close_panel_by_index_zero(ctl, mw, qapp):
    """close_panel(0) closes tab 0, not the current tab."""
    for name in ('a', 'b', 'c'):
        ctl.add_panel(DummyPanel(ctl, name))
    assert mw.tabs.count() == 3
    mw.tabs.setCurrentWidget(mw.tabs.widget(2))
    ctl.close_panel(0)
    names = [mw.tabs.tabText(i) for i in range(mw.tabs.count())]
    assert names == ['b', 'c']


def test_close_panel_none_closes_current(ctl, mw, qapp):
    for name in ('a', 'b'):
        ctl.add_panel(DummyPanel(ctl, name))
    mw.tabs.setCurrentIndex(0)
    ctl.close_panel()
    assert mw.tabs.count() == 1


def test_close_panel_by_widget(ctl, mw, qapp):
    a = DummyPanel(ctl, 'a')
    ctl.add_panel(a)
    ctl.close_panel(a)
    assert mw.tabs.count() == 0


def test_open_search_dedupes(ctl, mw, qapp, notmuch_stub):
    notmuch_stub.threads = [make_thread('t1', 'Hello')]
    ctl.open_search('tag:inbox')
    ctl.open_search('tag:inbox')
    # same query -> same tab, not a duplicate
    assert ctl.num_panels() == 1


def test_navigate_list_on_search_panel(ctl, mw, qapp, notmuch_stub):
    notmuch_stub.threads = [make_thread('t1', 'A'), make_thread('t2', 'B')]
    ctl.open_search('tag:inbox')
    sp = mw.tabs.currentWidget()
    ctl.navigate_list('next')
    assert sp.tree.currentIndex().row() == 1
    ctl.navigate_list('previous')
    assert sp.tree.currentIndex().row() == 0


def test_delegate_to_list_unknown_method_warns(ctl, mw, qapp, notmuch_stub, caplog):
    notmuch_stub.threads = [make_thread('t1', 'A')]
    ctl.open_search('tag:inbox')
    import logging
    with caplog.at_level(logging.WARNING):
        ctl.delegate_to_list('no_such_method')
    assert 'unknown method' in caplog.text


def test_tag_bar_prefills_plus(ctl, qapp):
    ctl.tag_bar('tag')
    bar = ctl.command_bar
    assert bar.toPlainText() == '+'
    assert bar.textCursor().position() == 1


def test_tag_bar_empty_expr_is_noop(ctl, mw, qapp, notmuch_stub):
    notmuch_stub.threads = [make_thread('t1', 'A')]
    ctl.open_search('tag:inbox')
    ctl.tag_bar('tag')
    ctl.command_bar.accept()
    assert notmuch_stub.tag_calls == []


def test_tag_bar_typed_expr_dispatches(ctl, mw, qapp, notmuch_stub):
    notmuch_stub.threads = [make_thread('t1', 'A')]
    ctl.open_search('tag:inbox')
    ctl.tag_bar('tag')
    ctl.command_bar.setPlainText('+work')
    ctl.command_bar.accept()
    assert len(notmuch_stub.tag_calls) == 1
    assert notmuch_stub.tag_calls[0][0] == '+work'


def test_mark_and_advance(ctl, mw, qapp, notmuch_stub):
    notmuch_stub.threads = [make_thread('t1', 'A'), make_thread('t2', 'B')]
    ctl.open_search('tag:inbox')
    ctl.mark_and_advance()
    assert notmuch_stub.tag_calls[0][0] == '+marked'
    assert mw.tabs.currentWidget().tree.currentIndex().row() == 1
