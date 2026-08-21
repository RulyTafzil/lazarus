"""Maildir move planning — pure planners + mbsync stale-path resolution.

Refactoring 3: ``move_to_trash`` / ``move_specific_files`` delegate their
per-account destination mapping to the pure ``plan_trash_moves`` /
``plan_archive_moves`` functions.  These tests exercise the pure mapping
(no QThread, no tagging, no ``mkdir``) and the mbsync race (a file
renamed after search results are returned) through the real move flow.

The worker-reconnection behaviour is deliberately *not* re-tested here —
it is already pinned by ``test_actions.py`` (``test_batch_done_listener_invoked``
and ``test_worker_runs_notmuch_new_after_batch``).
"""
import os
import time

from lazarus import actions


def _write(path, content='body\n'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)
    return path


def _wait_until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


# -- pure trash planning ----------------------------------------------------

def test_plan_trash_moves_multi_account(tmp_path, maildir):
    """Two accounts resolve to separate Trash folders under each root."""
    mail_root = str(tmp_path / 'Mail')
    f1 = _write(os.path.join(mail_root, 'acct1', 'INBOX', 'cur', 'm1:2,S'))
    f2 = _write(os.path.join(mail_root, 'acct2', 'INBOX', 'cur', 'm2:2,S'))
    moves = actions.plan_trash_moves([f1, f2], mail_root)
    assert len(moves) == 2
    by_src = dict(moves)
    assert by_src[f1] == os.path.join(mail_root, 'acct1', 'Trash', 'cur', 'm1:2,S')
    assert by_src[f2] == os.path.join(mail_root, 'acct2', 'Trash', 'cur', 'm2:2,S')


def test_plan_trash_moves_prefers_gmail_trash(tmp_path, maildir):
    """An existing [Gmail]/Trash folder wins over a plain Trash folder."""
    mail_root = str(tmp_path / 'Mail')
    _write(os.path.join(mail_root, 'acct', '[Gmail]', 'Trash', 'cur', 'marker'))
    _write(os.path.join(mail_root, 'acct', 'Trash', 'cur', 'marker'))
    f = _write(os.path.join(mail_root, 'acct', 'INBOX', 'cur', 'm:2,S'))
    moves = actions.plan_trash_moves([f], mail_root)
    assert len(moves) == 1
    assert moves[0][1] == os.path.join(
        mail_root, 'acct', '[Gmail]', 'Trash', 'cur', 'm:2,S')


def test_plan_trash_moves_is_pure_no_mkdir(tmp_path, maildir):
    """The planner computes paths but never creates directories."""
    mail_root = str(tmp_path / 'Mail')
    f = _write(os.path.join(mail_root, 'acct', 'INBOX', 'cur', 'm:2,S'))
    trash_dir = os.path.join(mail_root, 'acct', 'Trash', 'cur')
    assert not os.path.exists(trash_dir)
    moves = actions.plan_trash_moves([f], mail_root)
    assert len(moves) == 1
    assert not os.path.exists(trash_dir)  # pure: no mkdir side effect


def test_plan_trash_moves_skips_foreign_files(tmp_path, maildir):
    """Files outside the mail root are dropped from the plan."""
    mail_root = str(tmp_path / 'Mail')
    foreign = _write(os.path.join(str(tmp_path), 'var', 'tmp', 'x:2,S'))
    f = _write(os.path.join(mail_root, 'acct', 'INBOX', 'cur', 'm:2,S'))
    moves = actions.plan_trash_moves([f, foreign], mail_root)
    assert len(moves) == 1
    assert moves[0][0] == f


def test_plan_trash_moves_strips_uid_annotation(tmp_path, maildir):
    mail_root = str(tmp_path / 'Mail')
    f = _write(os.path.join(mail_root, 'acct', 'INBOX', 'cur', 'm,U=90:2,S'))
    moves = actions.plan_trash_moves([f], mail_root)
    assert len(moves) == 1
    assert 'U=90' not in moves[0][1]
    assert moves[0][1].endswith('m:2,S')


def test_plan_trash_moves_collision_suffix(tmp_path, maildir):
    """A basename collision on the dest gets a .1/.2 counter, no clobber."""
    mail_root = str(tmp_path / 'Mail')
    _write(os.path.join(mail_root, 'acct', 'Trash', 'cur', 'm:2,S'))  # collide
    f = _write(os.path.join(mail_root, 'acct', 'INBOX', 'cur', 'm:2,S'))
    moves = actions.plan_trash_moves([f], mail_root)
    assert len(moves) == 1
    # _unique_dest suffixes after the full basename (no clobber), not
    # between ``m`` and ``:2,S`` — the flag parse is unchanged by design.
    assert os.path.basename(moves[0][1]) == 'm:2,S.1'


# -- pure archive planning --------------------------------------------------

def test_plan_archive_moves_skips_in_target(tmp_path, maildir):
    archive = str(tmp_path / 'Archive')
    inside = os.path.join(archive, 'cur', 'm:2,S')
    _write(inside)
    moves = actions.plan_archive_moves([inside], archive)
    assert moves == []


def test_plan_archive_moves_collision_suffix(tmp_path, maildir):
    archive = str(tmp_path / 'Archive')
    _write(os.path.join(archive, 'cur', 'm:2,S'))  # collide
    src = _write(os.path.join(str(tmp_path), 'src', 'm:2,S'))
    moves = actions.plan_archive_moves([src], archive)
    assert len(moves) == 1
    assert os.path.basename(moves[0][1]) == 'm:2,S.1'


# -- mbsync race through the real move flow ---------------------------------

def test_move_to_trash_resolves_mbsync_flag_rename(notmuch_stub, maildir):
    """mbsync renamed msg:2, -> msg:2,S after the search; the move still
    lands because _resolve_stale_path finds it in the sibling folder."""
    cur = os.path.join(maildir, 'default', 'INBOX', 'cur')
    new = os.path.join(maildir, 'default', 'INBOX', 'new')
    stale = os.path.join(cur, 'msg-42:2,')
    actual = os.path.join(new, 'msg-42:2,S')
    _write(actual)
    # collect_files gets the stale 'cur' path; the real file lives in new/.
    notmuch_stub.files = [stale]
    n = actions.move_to_trash('tag:inbox')
    assert n == 1
    trash = os.path.join(maildir, 'default', 'Trash', 'cur')
    assert _wait_until(lambda: any('msg-42' in f for f in os.listdir(trash)))
    assert not os.path.exists(actual)
