"""actions — file moves, trash/archive/restore/expunge on a tmp Maildir.

The notmuch layer is stubbed; only the file-move logic runs for real.
File moves go through the background worker, so tests poll the
filesystem for the async result.
"""
import os
import time

import pytest

from lazarus import actions, settings


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
    assert actions._strip_uid_annotation('msg:2,S') == 'msg:2,S'
    assert actions._strip_uid_annotation('msg,U=123:2,S') == 'msg:2,S'


def test_unique_dest(tmp_path):
    p = tmp_path / 'f.txt'
    p.write_text('x')
    assert actions._unique_dest(str(p)).endswith('f.1.txt')
    q = tmp_path / 'f.1.txt'
    q.write_text('y')
    assert actions._unique_dest(str(p)).endswith('f.2.txt')


def test_resolve_stale_path_new_to_cur(maildir):
    cur = os.path.join(maildir, 'default', 'INBOX', 'cur')
    new = os.path.join(maildir, 'default', 'INBOX', 'new')
    # file lives in new/, path says cur/
    with open(os.path.join(new, 'msg-9:2,'), 'w') as f:
        f.write('x')
    stale = os.path.join(cur, 'msg-9:2,S')
    resolved = actions._resolve_stale_path(stale)
    assert resolved is not None
    assert os.path.basename(resolved).startswith('msg-9')


def test_mail_file_account(maildir):
    path = os.path.join(maildir, 'default', 'INBOX', 'cur', 'm')
    assert actions._mail_file_account(path) == ('default', 'INBOX/cur/m')
    assert actions._mail_file_account('/etc/hosts') is None


def test_check_archive_refused():
    assert actions.check_archive_refused({'inbox', 'unread'})
    assert not actions.check_archive_refused({'inbox', 'unread', 'work'})
    assert actions.check_archive_refused(set())


def test_is_trash_path():
    assert actions._is_trash_path('/Mail/gmail/[Gmail]/Trash/cur/x')
    assert actions._is_trash_path('/Mail/gmail/Trash/cur/x')
    assert not actions._is_trash_path('/Mail/gmail/INBOX/cur/x')


# -- move flows -------------------------------------------------------------

def test_move_to_trash_moves_file(notmuch_stub, maildir, inbox_file):
    notmuch_stub.files = [inbox_file]
    n = actions.move_to_trash('tag:inbox')
    assert n == 1
    assert notmuch_stub.tag_calls == [('+trash -inbox -unread', 'tag:inbox', True)]
    trash = os.path.join(maildir, 'default', 'Trash', 'cur')
    assert _wait_until(lambda: any('msg-1' in f for f in os.listdir(trash)))
    assert not os.path.exists(inbox_file)


def test_move_to_archive(notmuch_stub, maildir, inbox_file):
    notmuch_stub.files = [inbox_file]
    n = actions.move_to_archive('tag:inbox')
    assert n == 1
    archive_cur = os.path.join(maildir, 'Archive', 'cur')
    assert _wait_until(lambda: any('msg-1' in f for f in os.listdir(archive_cur)))


def test_restore_from_trash(notmuch_stub, maildir):
    trash_dir = os.path.join(maildir, 'default', 'Trash', 'cur')
    src = os.path.join(trash_dir, 'msg-5:2,S')
    with open(src, 'w') as f:
        f.write('x')
    notmuch_stub.files = [src]
    n = actions.restore_from_trash('tag:trash')
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
    n = actions.expunge_trash()
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
    assert actions.expunge_trash() == 0
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
    actions.move_to_trash('tag:inbox')
    deadline = time.time() + 5
    while time.time() < deadline and notmuch_stub.new_calls <= baseline:
        qapp.processEvents()
        time.sleep(0.02)
    assert notmuch_stub.new_calls == baseline + 1
