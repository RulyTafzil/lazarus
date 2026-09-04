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

from ned import actions


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


def test_worker_resolves_rename_after_planning(maildir):
    """A filename change that lands AFTER planning must not lose the move.

    The queue can hold a batch for a moment; notmuch flag-sync or a
    concurrent mbsync may rename the source in that window. The worker
    must follow the file by stem and move it anyway (regression for the
    NED daemon path where the tag lands but the file stays in INBOX).
    """
    cur = os.path.join(maildir, 'default', 'INBOX', 'cur')
    trash_cur = os.path.join(maildir, 'default', 'Trash', 'cur')
    src = _write(os.path.join(cur, 'msg-7,U=11:2,S'))
    moves = actions.plan_trash_moves([src], maildir)
    assert moves == [(src, os.path.join(trash_cur, 'msg-7:2,S'))]

    # External renamer strikes AFTER planning, BEFORE the worker runs.
    renamed = os.path.join(cur, 'msg-7,U=11:2,FS')
    os.rename(src, renamed)

    from ned import actions as core_actions
    core_actions.get_worker().enqueue(moves)
    assert _wait_until(lambda: any('msg-7' in f for f in os.listdir(trash_cur)))
    # Moved with the CURRENT flags, and nothing left in INBOX.
    assert os.path.exists(os.path.join(trash_cur, 'msg-7:2,FS'))
    assert not os.path.exists(renamed)


def test_resolve_stale_path_exact_stem_no_wrong_file(maildir, tmp_path):
    """Stale-path resolution matches the stem EXACTLY — never a prefix.

    A flag-change rename is the same message (`m-plain:2,RS` -> the file
    `m-plain:2,S` still present); a prefix-sharing sibling with a longer
    name is a different message and must NOT be claimed.
    """
    from ned.actions import _resolve_stale_path as rsp
    cur = os.path.join(maildir, 'default', 'INBOX', 'cur')
    _write(os.path.join(cur, 'm-plain:2,S'))
    _write(os.path.join(cur, 'm-plain_extra:2,S'))   # different message

    assert rsp(os.path.join(cur, 'm-plain:2,RS')) == os.path.join(cur, 'm-plain:2,S')
    # Intended file gone; only the prefix sibling (wrong file!) exists.
    assert rsp(os.path.join(cur, 'm-plain_extra:2,RS')) == os.path.join(cur, 'm-plain_extra:2,S')
    # A distinct stem with only a prefix-sharing unrelated file -> gone.
    assert rsp(os.path.join(cur, 'm-plainX:2,RS')) is None


def test_resolve_stale_path_with_uid_annotation_change(maildir):
    """mbsync adding or modifying ,U= annotations between new and cur is resolved."""
    from ned.actions import _resolve_stale_path as rsp
    new = os.path.join(maildir, 'default', 'INBOX', 'new')
    cur = os.path.join(maildir, 'default', 'INBOX', 'cur')
    # File was in new without UID, mbsync or sync moved it to cur with ,U=42:2,S
    _write(os.path.join(cur, 'msg-new-arrival,U=42:2,S'))

    resolved = rsp(os.path.join(new, 'msg-new-arrival'))
    assert resolved == os.path.join(cur, 'msg-new-arrival,U=42:2,S')


def test_move_to_trash_collects_before_tagging_with_unmark(notmuch_stub, maildir):
    """move_to_trash must collect files BEFORE tagging strips query tags."""
    cur = os.path.join(maildir, 'default', 'INBOX', 'cur')
    trash_cur = os.path.join(maildir, 'default', 'Trash', 'cur')
    src = _write(os.path.join(cur, 'msg-marked-batch,U=5:2,'))

    notmuch_stub.files = [src]
    # Simulate tagging stripping tag:marked so a second query would return empty
    def fake_tag(expr, query, exclude_marked=False):
        notmuch_stub.files = []  # Next search returns nothing
        return type("R", (), {"returncode": 0})()

    notmuch_stub.tag = fake_tag

    moved = actions.move_to_trash('tag:marked AND tag:inbox', unmark=True)
    assert moved == 1
    assert _wait_until(lambda: any('msg-marked-batch' in f for f in os.listdir(trash_cur)))
    assert not os.path.exists(src)
