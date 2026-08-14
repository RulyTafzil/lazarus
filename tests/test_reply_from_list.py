"""Reply/forward from the search list (r / R / C-y without opening)."""
import json
import subprocess

import pytest

from lazarus import mainwindow
from lazarus.controller import AppController
from lazarus.search import SearchPanel
from tests.conftest import make_thread, make_message


@pytest.fixture
def mw(qapp, fake_app, notmuch_stub):
    win = mainwindow.MainWindow(fake_app)
    win.resize(1000, 700)
    win.show()
    return win


@pytest.fixture
def ctl(mw, fake_app):
    return AppController(fake_app, mw)  # type: ignore[arg-type]


def _stub_show(notmuch_stub, monkeypatch, messages):
    """notmuch.show for a thread returns *messages* as one thread."""
    import lazarus.notmuch as nm

    def fake_run(*args, **kwargs):
        if args and args[0] == 'show':
            thread = [[m, []] for m in messages]  # each message its own tree
            out = json.dumps([thread])
        elif args and args[0] == 'count':
            out = '1\n'
        else:  # 'search' --output=messages
            out = json.dumps(['x'])
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr='')

    monkeypatch.setattr(nm, 'run', fake_run)


def _stub_show_empty(monkeypatch):
    import lazarus.notmuch as nm

    def fake_run(*args, **kwargs):
        if args and args[0] == 'show':
            out = '[]'
        elif args and args[0] == 'count':
            out = '1\n'
        else:
            out = json.dumps(['x'])
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr='')

    monkeypatch.setattr(nm, 'run', fake_run)


def _capture_open_compose(ctl, monkeypatch):
    """Spy on the controller's open_compose (panels hold the controller)."""
    calls: list = []
    monkeypatch.setattr(ctl, 'open_compose',
                        lambda mode='', msg=None: calls.append((mode, msg)))
    return calls


def _open_list(ctl, mw, notmuch_stub):
    notmuch_stub.threads = [make_thread('t1', 'Hello'), make_thread('t2', 'Bye')]
    ctl.open_search('tag:inbox')
    sp = mw.tabs.currentWidget()
    assert isinstance(sp, SearchPanel)
    return sp


def test_list_reply_uses_most_recent_message(ctl, mw, qapp, notmuch_stub,
                                             monkeypatch):
    older = make_message('m1', 'First', timestamp=100)
    newer = make_message('m2', 'Latest', timestamp=200)
    _stub_show(notmuch_stub, monkeypatch, [older, newer])
    sp = _open_list(ctl, mw, notmuch_stub)
    calls = _capture_open_compose(ctl, monkeypatch)

    sp.reply(to_all=True)
    mode, msg = calls[0]
    assert mode == 'replyall'
    assert msg['id'] == 'm2'  # most recent, not the first


def test_list_reply_plain(ctl, mw, qapp, notmuch_stub, monkeypatch):
    _stub_show(notmuch_stub, monkeypatch, [make_message('m1', 'Hi')])
    sp = _open_list(ctl, mw, notmuch_stub)
    calls = _capture_open_compose(ctl, monkeypatch)
    sp.reply(to_all=False)
    assert calls[0][0] == 'reply'


def test_list_forward(ctl, mw, qapp, notmuch_stub, monkeypatch):
    _stub_show(notmuch_stub, monkeypatch, [make_message('m1', 'Hi')])
    sp = _open_list(ctl, mw, notmuch_stub)
    calls = _capture_open_compose(ctl, monkeypatch)
    sp.forward()
    assert calls[0][0] == 'forward'


def test_reply_no_messages_warns(ctl, mw, qapp, notmuch_stub, monkeypatch):
    _stub_show_empty(monkeypatch)
    sp = _open_list(ctl, mw, notmuch_stub)
    calls = _capture_open_compose(ctl, monkeypatch)
    statuses: list = []
    monkeypatch.setattr(ctl, 'status_message',
                        lambda *a, **k: statuses.append(a))
    sp.reply()
    assert calls == []
    assert statuses[0][0] == 'No message to reply to'


def test_controller_reply_list_focused(ctl, mw, qapp, notmuch_stub, monkeypatch):
    """With the list focused (no preview), r replies the selected thread."""
    _stub_show(notmuch_stub, monkeypatch, [make_message('m1', 'Hi')])
    sp = _open_list(ctl, mw, notmuch_stub)
    calls = _capture_open_compose(ctl, monkeypatch)
    sp.tree.setFocus()
    qapp.processEvents()
    ctl.reply(to_all=False)
    assert calls[0][0] == 'reply'


def test_controller_reply_preview_focused(ctl, mw, qapp, notmuch_stub,
                                         monkeypatch):
    """With the thread preview focused, r replies its current message."""
    from tests.test_controller import FakeThreadPanel

    fake_panel = FakeThreadPanel(ctl)
    mw._active_thread = fake_panel
    # the preview must be inside the (shown) window for offscreen focus
    mw.thread_container.addWidget(fake_panel)
    mw.thread_container.setCurrentWidget(fake_panel)
    mw.thread_container.show()
    fake_panel.show()
    fake_panel.setFocus()
    qapp.processEvents()
    ctl.reply(to_all=True)
    assert fake_panel.reply_calls == [True]


def test_latest_message_helper():
    from lazarus.thread_model import latest_message
    m1 = make_message('m1', 'First', timestamp=100)
    m2 = make_message('m2', 'Latest', timestamp=300)
    m3 = make_message('m3', 'Middle', timestamp=200)
    thread = [[m1, [[m3, []], [m2, []]]]]
    assert latest_message(thread)['id'] == 'm2'
    assert latest_message([]) is None
