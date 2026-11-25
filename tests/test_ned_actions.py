"""Tests for ned.actions — file move planners, worker, expunge/restore."""
import os
import time
import pytest

from ned import actions
from ned import settings


def test_ned_worker_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(actions.notmuch, 'new', lambda no_hooks=True: None)

    src = tmp_path / 'source.msg'
    dst = tmp_path / 'target.msg'
    src.write_text('content')

    worker = actions._BulkMoveWorker()
    done_called = []
    worker.add_listener(lambda: done_called.append(1))
    worker.start()

    try:
        worker.enqueue([(str(src), str(dst))])
        assert worker.wait_idle(timeout=5.0)
        assert not src.exists()
        assert dst.exists()
        assert len(done_called) >= 1
    finally:
        worker.shutdown(timeout_ms=1000)


def test_plan_archive_moves():
    files = ['/home/user/Mail/default/INBOX/cur/msg1,U=100:2,S']
    moves = actions.plan_archive_moves(files, '/home/user/Mail/Archive')
    assert len(moves) == 1
    src, dst = moves[0]
    assert src == files[0]
    assert dst.endswith('/Archive/cur/msg1:2,S')


def test_plan_trash_moves():
    files = ['/home/user/Mail/work/INBOX/cur/msg2,U=200:2,S']
    moves = actions.plan_trash_moves(files, '/home/user/Mail')
    assert len(moves) == 1
    src, dst = moves[0]
    assert src == files[0]
    assert '/work/Trash/cur/msg2:2,S' in dst


def test_expunge_keeps_trash_tag_on_partial_failure(notmuch_stub, maildir, monkeypatch):
    """expunge_trash must not clear tag:trash globally if any file failed or was skipped."""
    trash_dir = os.path.join(maildir, 'default', 'Trash', 'cur')
    inbox_dir = os.path.join(maildir, 'default', 'INBOX', 'cur')
    os.makedirs(trash_dir, exist_ok=True)
    os.makedirs(inbox_dir, exist_ok=True)

    good = os.path.join(trash_dir, 'msg-good:2,S')
    bad_outside = os.path.join(inbox_dir, 'msg-outside:2,S')
    with open(good, 'w') as f:
        f.write('good')
    with open(bad_outside, 'w') as f:
        f.write('outside')

    notmuch_stub.files = [good, bad_outside]
    count = actions.expunge_trash()
    assert count == 1
    assert os.path.exists(os.path.join(trash_dir, 'msg-good:2,ST'))
    assert notmuch_stub.tag_calls == []

    # Also verify OSError on rename keeps tag:trash
    good2 = os.path.join(trash_dir, 'msg-good2:2,S')
    fail_rename = os.path.join(trash_dir, 'msg-fail:2,S')
    with open(good2, 'w') as f:
        f.write('good2')
    with open(fail_rename, 'w') as f:
        f.write('fail')

    real_rename = os.rename

    def flaky_rename(src, dst):
        if 'msg-fail' in src:
            raise OSError('permission denied')
        return real_rename(src, dst)

    monkeypatch.setattr(os, 'rename', flaky_rename)
    notmuch_stub.files = [good2, fail_rename]
    count2 = actions.expunge_trash()
    assert count2 == 1
    assert notmuch_stub.tag_calls == []
