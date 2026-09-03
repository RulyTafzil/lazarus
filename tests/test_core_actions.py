"""Tests for lazarus.core.actions."""
import os
import time
import pytest

from lazarus.core import actions
from lazarus import settings


def test_core_worker_lifecycle(tmp_path, monkeypatch):
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
