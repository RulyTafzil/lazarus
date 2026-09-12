"""actions — file moves, trash/archive/restore/expunge on a tmp Maildir.

The notmuch layer is stubbed; only the file-move logic runs for real.
File moves go through the background worker, so tests poll the
filesystem for the async result.
"""
import os
import time

import pytest

from lazarus import actions, settings
from ned import actions as ned_actions


def _wait_until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def inbox_file(maildir):
    """A real message file in Mail/default/INBOX/cur."""
    path = os.path.join(maildir, 'default', 'INBOX', 'cur', 'msg-1:2,S')
    with open(path, 'w') as f:
        f.write('From: a@b.c\nSubject: hi\n\nbody\n')
    return path


@pytest.fixture(autouse=True)
def stub_worker_notmuch(notmuch_stub):
    """The worker runs notmuch.new() after each batch — stub it."""
    return notmuch_stub


# -- pure helpers -----------------------------------------------------------

def test_strip_uid_annotation():
    assert ned_actions._strip_uid_annotation('msg:2,S') == 'msg:2,S'
    assert ned_actions._strip_uid_annotation('msg,U=123:2,S') == 'msg:2,S'


def test_unique_dest(tmp_path):
    p = tmp_path / 'f.txt'
    p.write_text('x')
    assert ned_actions._unique_dest(str(p)).endswith('f.1.txt')
    q = tmp_path / 'f.1.txt'
    q.write_text('y')
    assert ned_actions._unique_dest(str(p)).endswith('f.2.txt')


def test_resolve_stale_path_new_to_cur(maildir):
    cur = os.path.join(maildir, 'default', 'INBOX', 'cur')
    new = os.path.join(maildir, 'default', 'INBOX', 'new')
    # file lives in new/, path says cur/
    with open(os.path.join(new, 'msg-9:2,'), 'w') as f:
        f.write('x')
    stale = os.path.join(cur, 'msg-9:2,S')
    resolved = ned_actions._resolve_stale_path(stale)
    assert resolved is not None
    assert os.path.basename(resolved).startswith('msg-9')


def test_mail_file_account(maildir):
    path = os.path.join(maildir, 'default', 'INBOX', 'cur', 'm')
    assert ned_actions._mail_file_account(path) == ('default', 'INBOX/cur/m')
    assert ned_actions._mail_file_account('/etc/hosts') is None


def test_check_archive_refused():
    assert ned_actions.check_archive_refused({'inbox', 'unread'})
    assert not ned_actions.check_archive_refused({'inbox', 'unread', 'work'})
    assert ned_actions.check_archive_refused(set())


def test_is_trash_path():
    assert ned_actions._is_trash_path('/Mail/gmail/[Gmail]/Trash/cur/x')
    assert ned_actions._is_trash_path('/Mail/gmail/Trash/cur/x')
    assert not ned_actions._is_trash_path('/Mail/gmail/INBOX/cur/x')


# -- move flows -------------------------------------------------------------

def test_move_to_trash_moves_file(notmuch_stub, maildir, inbox_file):
    notmuch_stub.files = [inbox_file]
    n = ned_actions.move_to_trash('tag:inbox')
    assert n == 1
    assert notmuch_stub.tag_calls == [('+trash -inbox -unread', 'tag:inbox', True)]
    trash = os.path.join(maildir, 'default', 'Trash', 'cur')
    assert _wait_until(lambda: any('msg-1' in f for f in os.listdir(trash)))
    assert not os.path.exists(inbox_file)


def test_move_to_archive(notmuch_stub, maildir, inbox_file):
    notmuch_stub.files = [inbox_file]
    n = ned_actions.move_to_archive('tag:inbox')
    assert n == 1
    archive_cur = os.path.join(maildir, 'Archive', 'cur')
    assert _wait_until(lambda: any('msg-1' in f for f in os.listdir(archive_cur)))


def test_restore_from_trash(notmuch_stub, maildir):
    trash_dir = os.path.join(maildir, 'default', 'Trash', 'cur')
    src = os.path.join(trash_dir, 'msg-5:2,S')
    with open(src, 'w') as f:
        f.write('x')
    notmuch_stub.files = [src]
    n = ned_actions.restore_from_trash('tag:trash')
    assert n == 1
    assert notmuch_stub.tag_calls[0][:2] == ('-trash +inbox', 'tag:trash')
    inbox_cur = os.path.join(maildir, 'default', 'INBOX', 'cur')
    assert _wait_until(lambda: any('msg-5' in f for f in os.listdir(inbox_cur)))


def test_expunge_trash_appends_t_flag(notmuch_stub, maildir):
    trash_dir = os.path.join(maildir, 'default', 'Trash', 'cur')
    src = os.path.join(trash_dir, 'msg-7:2,S')
    with open(src, 'w') as f:
        f.write('x')
    notmuch_stub.files = [src]
    n = ned_actions.expunge_trash()
    assert n == 1
    assert notmuch_stub.tag_calls[0][:2] == ('-trash', 'tag:trash')
    names = os.listdir(trash_dir)
    assert any(name.endswith(':2,ST') for name in names)


def test_expunge_skips_already_trashed(notmuch_stub, maildir):
    trash_dir = os.path.join(maildir, 'default', 'Trash', 'cur')
    src = os.path.join(trash_dir, 'msg-8:2,ST')
    with open(src, 'w') as f:
        f.write('x')
    notmuch_stub.files = [src]
    assert ned_actions.expunge_trash() == 0
    assert notmuch_stub.tag_calls == []


def test_worker_runs_notmuch_new_after_batch(notmuch_stub, maildir, inbox_file, qapp):
    """After a move batch lands, notmuch new fires exactly once more.

    batch_done is emitted from the worker thread but the slot was
    connected in the main thread, so Qt queues it — the wait loop must
    process events.  The worker is a session singleton, so drain any
    batch_done queued by earlier tests before recording the baseline.
    """
    for _ in range(10):
        qapp.processEvents()
    baseline = notmuch_stub.new_calls
    notmuch_stub.files = [inbox_file]
    ned_actions.move_to_trash('tag:inbox')
    deadline = time.time() + 5
    while time.time() < deadline and notmuch_stub.new_calls <= baseline:
        qapp.processEvents()
        time.sleep(0.02)
    assert notmuch_stub.new_calls == baseline + 1


def test_delete_thread_skips_marked_check_when_none_marked(client_stub, inbox_file):
    class FakePanel(actions.MarkableActionsMixin):
        def __init__(self):
            self.app = type('App', (), {
                'update_single_thread': lambda *_: None,
                'status_message': lambda *_: None,
            })()
        def _has_marked_threads(self):
            return False
        def _marked_query(self):
            return 'tag:marked'
        def _current_thread_id(self):
            return 't123'
        def _current_thread_tags(self):
            return {'inbox'}

    p = FakePanel()
    p.delete_thread()
    # NED-only: the delete is dispatched to the daemon as a trash call for
    # the single thread — never as a local tag on the marked query.
    assert client_stub.trash_calls == ['t123']
    assert not any('tag:marked' in q for q in client_stub.trash_calls)


def test_delete_thread_deletes_current_before_advance(client_stub):
    """delete_thread must capture the thread ID before advancing selection."""
    class AdvancingPanel(actions.MarkableActionsMixin):
        def __init__(self):
            self.threads = ['thread-1', 'thread-2', 'thread-3']
            self.cursor = 0
            self.app = type('App', (), {
                'update_single_thread': lambda *_: None,
                'status_message': lambda *_: None,
            })()
        def _has_marked_threads(self):
            return False
        def _current_thread_id(self):
            if 0 <= self.cursor < len(self.threads):
                return self.threads[self.cursor]
            return None
        def _advance_selection(self):
            self.cursor += 1

    panel = AdvancingPanel()
    panel.delete_thread()
    # Must have trashed thread-1, and advanced cursor to thread-2
    assert client_stub.trash_calls == ['thread-1']
    assert panel.cursor == 1


def test_restore_thread_restores_current_before_advance(client_stub):
    """restore_thread_from_trash must capture the thread ID before advancing selection."""
    class AdvancingPanel(actions.MarkableActionsMixin):
        def __init__(self):
            self.threads = ['thread-1', 'thread-2', 'thread-3']
            self.cursor = 0
            self.app = type('App', (), {
                'update_single_thread': lambda *_: None,
                'status_message': lambda *_: None,
            })()
        def _has_marked_threads(self):
            return False
        def _current_thread_id(self):
            if 0 <= self.cursor < len(self.threads):
                return self.threads[self.cursor]
            return None
        def _advance_selection(self):
            self.cursor += 1

    panel = AdvancingPanel()
    panel.restore_thread_from_trash()
    assert client_stub.untrash_calls == ['thread-1']
    assert panel.cursor == 1


def test_plan_trash_moves_alternate_mail_root(tmp_path):
    """Files living in a different mail root (e.g. /mnt/Mail) must be trashed within their own root."""
    alt_root = tmp_path / 'alt_mount' / 'Mail'
    inbox_cur = alt_root / 'contact@example.com' / 'Inbox' / 'cur'
    inbox_cur.mkdir(parents=True, exist_ok=True)
    msg_file = inbox_cur / '12345.alpine,U=100:2,S'
    msg_file.write_text('From: test\n')

    moves = ned_actions.plan_trash_moves([str(msg_file)], mail_root=str(tmp_path / 'nonexistent' / 'Mail'))
    assert len(moves) == 1
    src, dst = moves[0]
    assert src == str(msg_file)
    expected_trash = str(alt_root / 'contact@example.com' / 'Trash' / 'cur' / '12345.alpine:2,S')
    assert dst == expected_trash


def test_restore_from_trash_alternate_mail_root(tmp_path, notmuch_stub):
    """Files restored from trash in an alternate mail root must return to their own account's Inbox."""
    alt_root = tmp_path / 'alt_mount' / 'Mail'
    trash_cur = alt_root / 'contact@example.com' / 'Trash' / 'cur'
    inbox_cur = alt_root / 'contact@example.com' / 'Inbox' / 'cur'
    trash_cur.mkdir(parents=True, exist_ok=True)
    inbox_cur.mkdir(parents=True, exist_ok=True)

    msg_file = trash_cur / 'msg-99:2,S'
    msg_file.write_text('From: test\n')
    notmuch_stub.files = [str(msg_file)]

    n = ned_actions.restore_from_trash('tag:trash')
    assert n == 1
    assert _wait_until(lambda: (inbox_cur / 'msg-99:2,S').exists())


def test_get_mail_root_falls_back_to_notmuch(monkeypatch, tmp_path):
    """get_mail_root queries notmuch if settings.mail_root does not exist."""
    fake_mail = tmp_path / 'notmuch_detected_mail'
    fake_mail.mkdir(parents=True, exist_ok=True)

    from ned import settings as ned_settings, notmuch
    monkeypatch.setattr(ned_settings, 'mail_root', str(tmp_path / 'does_not_exist'))

    class FakeRun:
        stdout = str(fake_mail) + '\n'

    monkeypatch.setattr(notmuch, 'run', lambda *args, **kwargs: FakeRun())
    assert ned_actions.get_mail_root() == str(fake_mail)


def test_search_panel_advance_selection_forward(qapp, fake_app, client_stub):
    """SearchPanel._advance_selection moves cursor to the next thread."""
    from lazarus.search import SearchPanel
    from tests.conftest import make_thread

    client_stub.threads = [
        make_thread('t1', 'Thread 1'),
        make_thread('t2', 'Thread 2'),
        make_thread('t3', 'Thread 3'),
    ]
    p = SearchPanel(fake_app, 'tag:inbox')
    assert p.tree.currentIndex().row() == 0
    p._advance_selection()
    assert p.tree.currentIndex().row() == 1
    p.close()
    p.deleteLater()
    qapp.processEvents()


def test_search_panel_advance_selection_at_end(qapp, fake_app, client_stub):
    """SearchPanel._advance_selection moves cursor to the previous thread if on the last row."""
    from lazarus.search import SearchPanel
    from tests.conftest import make_thread

    client_stub.threads = [
        make_thread('t1', 'Thread 1'),
        make_thread('t2', 'Thread 2'),
    ]
    p = SearchPanel(fake_app, 'tag:inbox')
    p.tree.setCurrentIndex(p.model.index(1, 0))
    assert p.tree.currentIndex().row() == 1
    p._advance_selection()
    assert p.tree.currentIndex().row() == 0
    p.close()
    p.deleteLater()
    qapp.processEvents()


def test_search_panel_advance_selection_opens_preview(qapp, fake_app, client_stub):
    """SearchPanel._advance_selection immediately opens the new thread preview if preview is open."""
    from lazarus.search import SearchPanel
    from tests.conftest import make_thread

    client_stub.threads = [
        make_thread('t1', 'Thread 1'),
        make_thread('t2', 'Thread 2'),
    ]
    fake_app.main_window.has_thread_preview = lambda: True
    p = SearchPanel(fake_app, 'tag:inbox')
    opened = []
    fake_app.open_thread = lambda tid, q: opened.append((tid, q))
    p._advance_selection()
    assert opened == [('t2', 'tag:inbox')]
    p.close()
    p.deleteLater()
    qapp.processEvents()



