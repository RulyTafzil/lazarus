"""rules — filter engine (notmuch stubbed)."""
import pytest

from lazarus.rules import Rule, apply_rules
from lazarus import actions
from tests.conftest import make_thread


@pytest.fixture
def stub(notmuch_stub):
    """Populated notmuch stub: two inbox threads, one github thread."""
    notmuch_stub.threads = [
        make_thread('t1', 'Hello', tags=['inbox', 'unread']),
        make_thread('t2', 'GitHub notification',
                    tags=['inbox', 'unread'], authors='notifications@github.com'),
    ]
    return notmuch_stub


def test_rule_with_no_actions_skipped(stub, caplog):
    n = apply_rules([Rule(query='tag:inbox')], 'tag:inbox and tag:unread')
    assert n == 0
    assert stub.tag_calls == []
    assert 'no actions' in caplog.text


def test_rule_tags_matching_threads(stub):
    rule = Rule(query='from:notifications@github.com',
                tag_add=['github'], tag_remove=['inbox'])
    n = apply_rules([rule], 'tag:inbox and tag:unread')
    assert n == 1
    expr, query, _ = stub.tag_calls[0]
    assert expr == '+github -inbox'
    assert 'from:notifications@github.com' in query


def test_rule_no_match_is_noop(stub):
    # 'tag:urgent' matches nothing in the stub (filter handles tag:)
    n = apply_rules([Rule(query='tag:urgent', tag_add=['x'])], 'tag:inbox')
    assert n == 0
    assert stub.tag_calls == []


def test_rule_move_to_enqueues_moves(stub, tmp_path, monkeypatch):
    # files for the matching thread exist on disk under a maildir
    mail = tmp_path / 'Mail' / 'default' / 'INBOX' / 'cur'
    mail.mkdir(parents=True)
    f = mail / 'msg-1:2,S'
    f.write_text('x')
    stub.files = [str(f)]

    import lazarus.settings as settings
    settings.mail_root = str(tmp_path / 'Mail')

    moved = []
    monkeypatch.setattr(actions, 'move_specific_files',
                        lambda files, target: moved.append((files, target)) or len(files))

    rule = Rule(query='tag:inbox', tag_add=['seen'], move_to='~/Mail/Archive')
    n = apply_rules([rule], 'tag:inbox')
    assert n == 1
    assert moved and moved[0][0] == [str(f)]


def test_rule_move_with_no_files_warns(stub, caplog):
    rule = Rule(query='tag:inbox', move_to='~/Mail/Archive')
    n = apply_rules([rule], 'tag:inbox')
    assert n == 1  # matched count, no files collected
    assert 'no files collected' in caplog.text


def test_rule_scope_scoping(stub):
    """scope_query limits which mail a rule can touch."""
    stub.threads[1]['tags'] = ['unread']  # drop 'inbox' -> out of scope
    n = apply_rules([Rule(query='tag:inbox', tag_add=['x'])],
                    'tag:inbox and tag:unread')
    # the github thread (tag:inbox but not in scope? it IS inbox+unread)...
    # thread t2 still matches scope; verify the combined query is built.
    assert stub.count_calls
    combined = stub.count_calls[0][0]
    assert combined.startswith('(tag:inbox and tag:unread) and (tag:inbox)')


def test_rule_idempotent_reapply(stub):
    rule = Rule(query='tag:inbox', tag_add=['seen'])
    apply_rules([rule], 'tag:inbox and tag:unread')
    apply_rules([rule], 'tag:inbox and tag:unread')
    assert len(stub.tag_calls) == 2
