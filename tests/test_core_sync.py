"""Tests for lazarus.core.sync."""
from unittest.mock import MagicMock
import pytest

from ned.sync import (
    SyncResult,
    parse_sync_stats,
    format_sync_summary,
    run_sync,
)
from ned import notmuch, rules, settings


def test_parse_sync_stats():
    summaries = [
        "account1: Channels: Far: +3 *1 #0 -2",
        "account2: Channels: Far: +10 *0 #5 -1",
    ]
    new, flagged, expunged, deleted = parse_sync_stats(summaries)
    assert new == 13
    assert flagged == 1
    assert expunged == 5
    assert deleted == 3


def test_format_sync_summary():
    assert format_sync_summary(0, 0, 0, 0) == "Sync completed (no new mail)"
    msg = format_sync_summary(2, 1, 0, 4)
    assert "+2 new" in msg
    assert "*1 flagged" in msg
    assert "4 deleted" in msg
    assert "cleaned" not in msg


def test_run_sync_fallback_command(monkeypatch):
    called = []
    monkeypatch.setattr(notmuch, 'new', lambda no_hooks=False: called.append('new'))
    
    settings.smtp_accounts = []
    settings.sync_mail_command = 'echo "Sync test"'

    progress_messages = []
    result = run_sync(
        progress_callback=progress_messages.append,
        apply_rules=False,
    )

    assert result.ok is True
    assert result.sync_rc == 0
    assert result.notmuch_rc == 0
    assert 'new' in called
    assert any('Sync test' in m or 'Indexing' in m for m in progress_messages)


def test_run_sync_cancelled(monkeypatch):
    settings.smtp_accounts = []
    settings.sync_mail_command = 'sleep 10'

    result = run_sync(
        cancel_check=lambda: True,
        apply_rules=False,
    )
    assert result.ok is False
    assert result.sync_rc == 130
    assert 'cancelled' in result.message.lower()


def test_run_sync_applies_rules(monkeypatch):
    monkeypatch.setattr(notmuch, 'new', lambda no_hooks=False: None)
    settings.smtp_accounts = []
    settings.sync_mail_command = 'echo ok'

    rule_applied = []
    rule = rules.Rule(query='tag:test', tag_add=['processed'], name='Test Rule')
    settings.filter_rules = [rule]
    settings.filter_scope_query = 'tag:inbox'

    monkeypatch.setattr(rules, 'apply_rules', lambda r, q: rule_applied.append((r, q)))

    result = run_sync(apply_rules=True)
    assert result.ok is True
    assert len(rule_applied) == 1
    assert rule_applied[0][1] == 'tag:inbox'
