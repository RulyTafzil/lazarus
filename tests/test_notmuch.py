"""lazarus.notmuch wrapper — arg construction and parsing."""
import subprocess
from unittest.mock import MagicMock

import pytest

import lazarus.notmuch as nm


@pytest.fixture
def fake_run(monkeypatch):
    """Replace notmuch.run with a recorder returning a fake result."""
    def _make(result=None, returncode=0, stdout='', stderr=''):
        res = MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)
        if result is not None:
            return result
        return res

    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return _make()

    monkeypatch.setattr(nm, 'run', run)
    return calls


def test_count_parses_int(monkeypatch):
    monkeypatch.setattr(nm, 'run', lambda *a, **k: MagicMock(
        returncode=0, stdout='42\n', stderr=''))
    assert nm.count('tag:inbox') == 42


def test_count_returns_zero_on_garbage(monkeypatch):
    monkeypatch.setattr(nm, 'run', lambda *a, **k: MagicMock(
        returncode=1, stdout='not a number', stderr='boom'))
    assert nm.count('tag:inbox') == 0


def test_count_passes_output_flag(monkeypatch):
    captured = {}

    def run(*args, **kwargs):
        captured['args'] = args
        return MagicMock(returncode=0, stdout='3\n', stderr='')

    monkeypatch.setattr(nm, 'run', run)
    nm.count('tag:trash', output='files')
    assert captured['args'] == ('count', '--output=files', '--', 'tag:trash')


def _patch_subprocess_run(monkeypatch, stdout='', returncode=0):
    """Patch subprocess.run (count_batch calls it directly, not via run)."""
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], returncode,
                                           stdout=stdout, stderr='')
    monkeypatch.setattr(subprocess, 'run', fake_run)


def test_count_batch_parses_lines(monkeypatch):
    _patch_subprocess_run(monkeypatch, stdout='1\n2\n3\n')
    assert nm.count_batch(['a', 'b', 'c']) == [1, 2, 3]


def test_count_batch_pads_missing_lines(monkeypatch):
    _patch_subprocess_run(monkeypatch, stdout='1\n')
    assert nm.count_batch(['a', 'b']) == [1, 0]


def test_count_batch_zeros_on_error(monkeypatch):
    _patch_subprocess_run(monkeypatch, returncode=1)
    assert nm.count_batch(['a', 'b']) == [0, 0]


def test_count_batch_empty_query_list(monkeypatch):
    assert nm.count_batch([]) == []


def test_tags_splits_lines(monkeypatch):
    monkeypatch.setattr(nm, 'run', lambda *a, **k: MagicMock(
        returncode=0, stdout='inbox\nunread\nflagged\n', stderr=''))
    assert nm.tags() == ['inbox', 'unread', 'flagged']


def test_tag_builds_command(monkeypatch):
    captured = {}

    def run(*args, **kwargs):
        captured['args'] = args
        return MagicMock(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(nm, 'run', run)
    nm.tag('+trash -inbox', 'tag:inbox', exclude_marked=True)
    assert captured['args'] == (
        'tag', '+trash', '-inbox', '-marked', '--', 'tag:inbox')


def test_tag_without_exclude_marked(monkeypatch):
    captured = {}

    def run(*args, **kwargs):
        captured['args'] = args
        return MagicMock(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(nm, 'run', run)
    nm.tag('+work', 'thread:abc')
    assert captured['args'] == ('tag', '+work', '--', 'thread:abc')


def test_show_part_captures_bytes(monkeypatch):
    """show_part returns raw bytes, not text (attachments are binary)."""
    captured = {}

    def fake_run(args, **kwargs):
        captured['args'] = args
        captured['kwargs'] = kwargs
        return subprocess.CompletedProcess(
            args, 0, stdout=b'\x89PNG\r\n\x1a\n', stderr=b'')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    out = nm.show_part(7, 'msgid123')
    assert out == b'\x89PNG\r\n\x1a\n'
    assert captured['args'] == [
        'show', '--part', '7', '--decrypt=true', '--', 'id:msgid123']
    assert captured['kwargs'] == {'stdout': subprocess.PIPE, 'check': True}


def test_show_part_skips_decrypt_flag_when_disabled(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured['args'] = args
        return subprocess.CompletedProcess(args, 0, stdout=b'data')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    nm.show_part(3, 'id1', decrypt=False)
    assert captured['args'] == ['show', '--part', '3', '--', 'id:id1']


def test_search_files_passes_exclude_false(monkeypatch):
    captured = {}

    def run(*args, **kwargs):
        captured['args'] = args
        return MagicMock(returncode=0, stdout='/a\n/b\n', stderr='')

    monkeypatch.setattr(nm, 'run', run)
    nm.search_files('tag:trash', exclude_false=True)
    assert captured['args'] == (
        'search', '--exclude=false', '--output=files', '--', 'tag:trash')


def test_search_files_without_exclude_flag(monkeypatch):
    captured = {}

    def run(*args, **kwargs):
        captured['args'] = args
        return MagicMock(returncode=0, stdout='/a\n', stderr='')

    monkeypatch.setattr(nm, 'run', run)
    nm.search_files('tag:trash')
    assert captured['args'] == ('search', '--output=files', '--', 'tag:trash')


def test_search_json_invocation(monkeypatch):
    captured = {}

    def run(*args, **kwargs):
        captured['args'] = args
        return MagicMock(returncode=0, stdout='[]', stderr='')

    monkeypatch.setattr(nm, 'run', run)
    assert nm.search_json('tag:inbox') == '[]'
    assert captured['args'] == (
        'search', '--format=json', '--', 'tag:inbox')


def test_new_passes_no_hooks(monkeypatch):
    captured = {}

    def run(*args, **kwargs):
        captured['args'] = args
        return MagicMock(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(nm, 'run', run)
    nm.new()
    assert captured['args'] == ('new', '--no-hooks')


def test_run_wraps_subprocess(monkeypatch):
    """run() is the real subprocess shim — verify it forwards."""
    captured = {}

    def fake_subprocess_run(*args, **kwargs):
        captured['args'] = args
        captured['kwargs'] = kwargs
        return subprocess.CompletedProcess(args[0], 0, stdout='ok', stderr='')

    monkeypatch.setattr(subprocess, 'run', fake_subprocess_run)
    res = nm.run('search', '--format=json', '--', 'tag:inbox')
    assert res.returncode == 0
    assert res.stdout == 'ok'
    # run() forwards a single list command to subprocess.run
    cmd = captured['args'][0]
    assert cmd == ['notmuch', 'search', '--format=json', '--', 'tag:inbox']
    assert captured['kwargs']['capture_output'] is True
    assert captured['kwargs']['text'] is True
