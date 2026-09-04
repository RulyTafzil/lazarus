"""Reply/forward from the search list (r / R / C-y without opening)."""
import pytest

from lazarus import mainwindow
from lazarus.controller import AppController
from lazarus.search import SearchPanel
from tests.conftest import make_thread, make_message


@pytest.fixture
def mw(qapp, fake_app, client_stub):
    win = mainwindow.MainWindow(fake_app)
    win.resize(1000, 700)
    win.show()
    return win


@pytest.fixture
def ctl(mw, fake_app):
    return AppController(fake_app, mw)  # type: ignore[arg-type]


def _stub_show(client_stub, messages):
    """NED get_thread returns *messages* as one thread's roots."""
    client_stub.thread_trees['t1'] = [[[m, []] for m in messages]]


def _stub_show_empty(client_stub):
    """NED returns no messages for the thread."""
    client_stub.thread_trees['t1'] = []


def _capture_open_compose(ctl, monkeypatch):
    """Spy on the controller's open_compose (panels hold the controller)."""
    calls: list = []
    monkeypatch.setattr(ctl, 'open_compose',
                        lambda mode='', msg=None: calls.append((mode, msg)))
    return calls


def _open_list(ctl, mw, client_stub):
    client_stub.threads = [make_thread('t1', 'Hello'), make_thread('t2', 'Bye')]
    ctl.open_search('tag:inbox')
    sp = mw.tabs.currentWidget()
    assert isinstance(sp, SearchPanel)
    return sp


def test_list_reply_uses_most_recent_message(ctl, mw, qapp, client_stub,
                                             monkeypatch):
    older = make_message('m1', 'First', timestamp=100)
    newer = make_message('m2', 'Latest', timestamp=200)
    _stub_show(client_stub, [older, newer])
    sp = _open_list(ctl, mw, client_stub)
    calls = _capture_open_compose(ctl, monkeypatch)

    sp.reply(to_all=True)
    mode, msg = calls[0]
    assert mode == 'replyall'
    assert msg['id'] == 'm2'  # most recent, not the first


def test_list_reply_plain(ctl, mw, qapp, client_stub, monkeypatch):
    _stub_show(client_stub, [make_message('m1', 'Hi')])
    sp = _open_list(ctl, mw, client_stub)
    calls = _capture_open_compose(ctl, monkeypatch)
    sp.reply(to_all=False)
    assert calls[0][0] == 'reply'


def test_list_forward(ctl, mw, qapp, client_stub, monkeypatch):
    _stub_show(client_stub, [make_message('m1', 'Hi')])
    sp = _open_list(ctl, mw, client_stub)
    calls = _capture_open_compose(ctl, monkeypatch)
    sp.forward()
    assert calls[0][0] == 'forward'


def test_list_reply_quotes_html_only_email(ctl, mw, qapp, client_stub,
                                          monkeypatch):
    """Reply-from-list to an HTML-only email must quote its body.

    NED's thread fetch always requests ``--include-html`` (see
    ``test_ned_include_html_on_thread_fetch`` in test_ned.py), so the list
    path receives the HTML part and the reply quotes its plaintext form.
    """
    from lazarus import compose_model

    html_only = {
        'id': 'html1', 'timestamp': 200,
        'headers': {
            'Subject': 'HTML mail', 'From': 'Alice <alice@example.com>',
            'To': 'Bob <bob@example.com>',
            'Date': 'Thu, 01 Jan 1970 00:00:00 +0000',
        },
        'body': [{'id': 1, 'content-type': 'text/html',
                  'content': '<p>Hello <b>world</b></p>'}],
        'tags': ['inbox'], 'crypto': {}, 'match': True,
        'filename': ['/tmp/html1'], 'content-type': 'text/html',
    }
    _stub_show(client_stub, [html_only])
    sp = _open_list(ctl, mw, client_stub)
    calls = _capture_open_compose(ctl, monkeypatch)

    sp.reply(to_all=False)

    mode, msg = calls[0]
    assert mode == 'reply'
    seed = compose_model.build_reply_seed(msg, to_all=False)
    assert seed.body            # HTML part was fetched, not elided
    assert 'Hello' in seed.body
    assert '> Hello' in seed.body   # quoted


def test_reply_no_messages_warns(ctl, mw, qapp, client_stub, monkeypatch):
    _stub_show_empty(client_stub)
    sp = _open_list(ctl, mw, client_stub)
    calls = _capture_open_compose(ctl, monkeypatch)
    statuses: list = []
    monkeypatch.setattr(ctl, 'status_message',
                        lambda *a, **k: statuses.append(a))
    sp.reply()
    assert calls == []
    assert statuses[0][0] == 'No message to reply to'


def test_controller_reply_list_focused(ctl, mw, qapp, client_stub, monkeypatch):
    """With the list focused (no preview), r replies the selected thread."""
    _stub_show(client_stub, [make_message('m1', 'Hi')])
    sp = _open_list(ctl, mw, client_stub)
    calls = _capture_open_compose(ctl, monkeypatch)
    sp.tree.setFocus()
    qapp.processEvents()
    ctl.reply(to_all=False)
    assert calls[0][0] == 'reply'


def test_controller_reply_preview_focused(ctl, mw, qapp, client_stub,
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
