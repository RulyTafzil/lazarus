#     Lazarus - A fork of Dodo, a graphical, hackable email client based on notmuch
#     Copyright (C) 2026 - Ruly Tafzil
#
# This file is part of Lazarus
#
# Lazarus is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Lazarus is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Lazarus. If not, see <https://www.gnu.org/licenses/>.
"""Unit tests for NED post-sync plugin runner and TypeSafe Jev plugin."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import unittest.mock as mock
import pytest

from ned.plugins.base import PostSyncContext
from ned.plugins.runner import PluginRunner, resolve_plugin
from plugins.typesafe_jev import TypeSafeJevPlugin
import ned.settings


# =============================================================================
# PostSyncContext Tests
# =============================================================================

def test_post_sync_context_invalidate():
    """Verify context.invalidate passes parameters to invalidate_fn."""
    invocations = []

    def mock_invalidate(scope, item_id=None, reason=None):
        invocations.append((scope, item_id, reason))

    logger = logging.getLogger("test")
    ctx = PostSyncContext(
        stats={"new": 1},
        client=None,
        logger=logger,
        invalidate_fn=mock_invalidate,
    )

    ctx.invalidate(scope="threads", item_id="thread-123", reason="testing")
    assert invocations == [("threads", "thread-123", "testing")]

    ctx.invalidate()
    assert invocations[1] == ("threads", None, None)


def test_post_sync_context_invalidate_exception_handled():
    """Verify exceptions inside invalidate_fn do not crash the context caller."""
    def bad_invalidate(scope, item_id=None, reason=None):
        raise RuntimeError("SSE broadcast failure")

    logger = logging.getLogger("test")
    ctx = PostSyncContext(
        stats={},
        client=None,
        logger=logger,
        invalidate_fn=bad_invalidate,
    )

    # Should not raise
    ctx.invalidate(scope="threads")


# =============================================================================
# Plugin Resolution Tests
# =============================================================================

class DummyPlugin:
    name = "dummy"

    def __init__(self):
        self.called = False

    def on_post_sync(self, ctx: PostSyncContext) -> None:
        self.called = True


def dummy_callable_plugin(ctx: PostSyncContext) -> None:
    pass


def test_resolve_plugin_instance():
    instance = DummyPlugin()
    resolved = resolve_plugin(instance)
    assert resolved is instance


def test_resolve_plugin_class():
    resolved = resolve_plugin(DummyPlugin)
    assert isinstance(resolved, DummyPlugin)


def test_resolve_plugin_callable():
    resolved = resolve_plugin(dummy_callable_plugin)
    assert resolved is dummy_callable_plugin


def test_resolve_plugin_string_colon():
    resolved = resolve_plugin("plugins.typesafe_jev:TypeSafeJevPlugin")
    assert isinstance(resolved, TypeSafeJevPlugin)


def test_resolve_plugin_string_dot():
    resolved = resolve_plugin("plugins.typesafe_jev.TypeSafeJevPlugin")
    assert isinstance(resolved, TypeSafeJevPlugin)


def test_resolve_plugin_from_user_config_plugins_dir(tmp_path: Path):
    """Verify resolve_plugin finds plugins placed in ~/.config/ned/plugins/."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    plugin_file = plugin_dir / "user_custom_plugin.py"
    plugin_file.write_text(
        """
class CustomHook:
    name = "custom_hook"
    def on_post_sync(self, ctx):
        pass
""",
        encoding="utf-8",
    )

    with mock.patch("os.path.expanduser", side_effect=lambda p: str(plugin_dir) if "plugins" in p else p):
        resolved = resolve_plugin("user_custom_plugin.CustomHook")
        assert resolved is not None
        assert resolved.__class__.__name__ == "CustomHook"
        assert getattr(resolved, "name", None) == "custom_hook"


# =============================================================================
# PluginRunner Tests
# =============================================================================

def test_plugin_runner_lifecycle():
    """Verify start and stop methods are safe and idempotent."""
    runner = PluginRunner()
    assert runner._thread is None

    runner.start()
    assert runner._thread is not None
    assert runner._thread.is_alive()

    # Second start is idempotent
    first_thread = runner._thread
    runner.start()
    assert runner._thread is first_thread

    runner.stop(timeout=1.0)
    assert runner._thread is None

    # Second stop is idempotent
    runner.stop()


def test_plugin_runner_executes_plugin():
    """Verify enqueued tasks run registered plugins."""
    received_stats = []
    invalidations = []

    class TrackingPlugin:
        name = "tracker"

        def on_post_sync(self, ctx: PostSyncContext) -> None:
            received_stats.append(ctx.stats)
            ctx.invalidate(scope="threads", reason="tracked")

    plugin = TrackingPlugin()

    runner = PluginRunner(
        plugins_provider=lambda: [plugin],
        invalidate_fn=lambda s, i, r: invalidations.append((s, i, r)),
    )

    runner.enqueue({"new": 3, "flagged": 0, "expunged": 0, "deleted": 0})
    runner.drain()
    runner.stop()

    assert received_stats == [{"new": 3, "flagged": 0, "expunged": 0, "deleted": 0}]
    assert invalidations == [("threads", None, "tracked")]


def test_plugin_runner_supports_callable():
    """Verify callable plugins are executed if they don't have on_post_sync."""
    called_with = []

    def my_plugin(ctx: PostSyncContext):
        called_with.append(ctx.stats.get("new"))

    runner = PluginRunner(plugins_provider=lambda: [my_plugin])
    runner.enqueue({"new": 5})
    runner.drain()
    runner.stop()

    assert called_with == [5]


def test_plugin_runner_error_resilience():
    """Verify a failing plugin does not crash the runner or affect other plugins."""
    results = []

    class FailingPlugin:
        name = "fail"

        def on_post_sync(self, ctx: PostSyncContext) -> None:
            raise ValueError("Something broke in fail plugin")

    class HealthyPlugin:
        name = "healthy"

        def on_post_sync(self, ctx: PostSyncContext) -> None:
            results.append("healthy_done")

    runner = PluginRunner(plugins_provider=lambda: [FailingPlugin(), HealthyPlugin()])
    runner.enqueue({"new": 1})
    runner.drain()
    runner.stop()

    assert results == ["healthy_done"]


# =============================================================================
# TypeSafeJevPlugin Tests
# =============================================================================

def test_typesafe_jev_taxonomy_safety():
    """Verify default taxonomy contains empty API_KEY and no sensitive credentials."""
    tax_content = (Path(__file__).parent.parent / "plugins" / "typesafe_jev" / "taxonomy.py").read_text(encoding="utf-8")
    assert 'API_KEY = ""' in tax_content
    assert "ts_" not in tax_content


def test_typesafe_jev_loads_default_taxonomy():
    """Verify plugin loads default taxonomy.py located next to plugin."""
    plugin = TypeSafeJevPlugin()
    data = plugin.load_taxonomy()

    assert data["api_key"] is None  # empty string converted to None
    assert "Statements" in data["doc_types"]
    assert "Utilities" in data["domains"]
    assert data["confidence_threshold"] == 0.60
    assert data["trash_threshold"] == 0.70
    assert data["tagged_marker"] == "ai-tagged"


def test_typesafe_jev_custom_taxonomy(tmp_path: Path):
    """Verify custom taxonomy rubrics and API_KEY defined by the user are strictly respected."""
    tax_path = tmp_path / "taxonomy.py"
    tax_path.write_text(
        """
API_KEY = "test-custom-key-from-taxonomy"
DOC_TYPE_CRITERIA = {"Invoice": "Custom invoice rubric"}
DOMAIN_CRITERIA = {"Work": "Custom work domain"}
CONFIDENCE_THRESHOLD = 0.85
TRASH_THRESHOLD = 0.90
TAGGED_MARKER = "custom-tagged"
""",
        encoding="utf-8",
    )

    plugin = TypeSafeJevPlugin(taxonomy_path=str(tax_path))
    data = plugin.load_taxonomy()

    assert data["api_key"] == "test-custom-key-from-taxonomy"
    assert data["doc_types"] == {"Invoice": "Custom invoice rubric"}
    assert data["domains"] == {"Work": "Custom work domain"}
    assert data["confidence_threshold"] == 0.85
    assert data["trash_threshold"] == 0.90
    assert data["tagged_marker"] == "custom-tagged"


def test_typesafe_jev_malformed_taxonomy_raises(tmp_path: Path):
    """Verify missing criteria dictionaries in taxonomy.py raises ValueError."""
    tax_path = tmp_path / "taxonomy.py"
    tax_path.write_text("CONFIDENCE_THRESHOLD = 0.50\n", encoding="utf-8")

    plugin = TypeSafeJevPlugin(taxonomy_path=str(tax_path))
    with pytest.raises(ValueError, match="must define DOC_TYPE_CRITERIA"):
        plugin.load_taxonomy()


def test_typesafe_jev_decide_tags():
    """Verify tag assignment rules based on taxonomy thresholds."""
    plugin = TypeSafeJevPlugin()
    taxonomy = {
        "confidence_threshold": 0.60,
        "trash_threshold": 0.70,
        "tagged_marker": "ai-tagged",
    }

    # 1. Statements + Utilities with high confidence
    eval_res = {
        "doc_type": "Statements",
        "doc_type_conf": 0.95,
        "domain": "Utilities",
        "domain_conf": 0.90,
    }
    add, remove = plugin.decide_tags(eval_res, [], taxonomy)
    assert "Statements" in add
    assert "Utilities" in add
    assert "ai-tagged" in add
    assert remove == []

    # 2. ai-trash with confidence >= trash threshold
    eval_res = {
        "doc_type": "ai-trash",
        "doc_type_conf": 0.85,
        "domain": "None",
        "domain_conf": 0.0,
    }
    add, remove = plugin.decide_tags(eval_res, [], taxonomy)
    assert add == ["ai-trash", "ai-tagged"]
    assert remove == []

    # 3. ai-trash below threshold should not be trashed
    eval_res = {
        "doc_type": "ai-trash",
        "doc_type_conf": 0.65,  # below 0.70
        "domain": "None",
        "domain_conf": 0.0,
    }
    add, remove = plugin.decide_tags(eval_res, [], taxonomy)
    assert add == ["ai-tagged"]
    assert remove == []

    # 4. Personal correspondence does not receive archive tags
    eval_res = {
        "doc_type": "Personal",
        "doc_type_conf": 0.88,
        "domain": "None",
        "domain_conf": 0.0,
    }
    add, remove = plugin.decide_tags(eval_res, [], taxonomy)
    assert add == ["ai-tagged"]
    assert remove == []

    # 5. Low confidence below threshold -> receives no category tag, only ai-tagged
    eval_res = {
        "doc_type": "Support",
        "doc_type_conf": 0.40,
        "domain": "Housing",
        "domain_conf": 0.35,
    }
    add, remove = plugin.decide_tags(eval_res, [], taxonomy)
    assert add == ["ai-tagged"]
    assert remove == []


def test_typesafe_jev_evaluate_email_mocked():
    """Verify evaluate_email serializes only sender and subject, and parses response."""
    plugin = TypeSafeJevPlugin()

    mock_response_json = json.dumps({
        "model": "jev-latest",
        "answers": {
            "doc_type": {"choice": "Receipts", "confidence": 0.92},
            "domain": {"choice": "None", "confidence": 0.0},
        },
    }).encode("utf-8")

    class MockHTTPResponse:
        def __init__(self, data):
            self.data = data
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    with mock.patch("urllib.request.urlopen", return_value=MockHTTPResponse(mock_response_json)) as mock_urlopen:
        res = plugin.evaluate_email(
            api_key="test-api-key",
            sender="orders@store.com",
            subject="Your Order #12345",
            doc_type_criteria={"Receipts": "Receipts"},
            domain_criteria={"None": "None"},
        )

        assert res["doc_type"] == "Receipts"
        assert res["doc_type_conf"] == 0.92
        assert res["domain"] == "None"

        # Verify request sent
        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer test-api-key"
        payload = json.loads(req.data.decode("utf-8"))
        assert payload["state"] == {"from": "orders@store.com", "subject": "Your Order #12345"}
        # Ensure message bodies are never present
        assert "body" not in payload["state"]
        assert "content" not in payload["state"]


def test_typesafe_jev_on_post_sync_flow(tmp_path: Path):
    """Verify on_post_sync searches untagged threads, classifies them, modifies tags, and invalidates."""
    tax_path = tmp_path / "taxonomy.py"
    tax_path.write_text(
        """
DOC_TYPE_CRITERIA = {"Statements": "Statements"}
DOMAIN_CRITERIA = {"Utilities": "Utilities"}
""",
        encoding="utf-8",
    )
    plugin = TypeSafeJevPlugin(
        taxonomy_path=str(tax_path),
        api_key="fake-key",
    )

    threads = [
        {
            "thread": "thread_abc123",
            "authors": "billing@pge.com",
            "subject": "Your PG&E Energy Statement is Ready",
            "tags": ["inbox", "unread"],
        }
    ]

    mock_client = mock.MagicMock()
    del mock_client.modify_tags_batch
    mock_client.search_threads.return_value = threads
    mock_client.modify_tags.return_value = True

    invalidations = []
    logger = logging.getLogger("test.typesafe_jev")
    ctx = PostSyncContext(
        stats={"new": 1},
        client=mock_client,
        logger=logger,
        invalidate_fn=lambda s, i, r: invalidations.append((s, i, r)),
    )

    with mock.patch.object(
        plugin,
        "evaluate_email",
        return_value={
            "doc_type": "Statements",
            "doc_type_conf": 0.95,
            "domain": "Utilities",
            "domain_conf": 0.92,
        },
    ):
        plugin.on_post_sync(ctx)

    mock_client.search_threads.assert_called_once()
    mock_client.modify_tags.assert_called_once_with(
        threads=["thread_abc123"],
        add=["Statements", "Utilities", "ai-tagged"],
        add_tags=["Statements", "Utilities", "ai-tagged"],
    )
    assert invalidations == [("threads", None, "plugin:typesafe_jev")]


def test_typesafe_jev_skips_when_no_api_key(tmp_path: Path):
    """Verify on_post_sync gracefully exits without searching when no API key is present."""
    plugin = TypeSafeJevPlugin(taxonomy_path=str(tmp_path / "tax.py"), api_key=None)

    mock_client = mock.MagicMock()
    ctx = PostSyncContext(
        stats={"new": 1},
        client=mock_client,
        logger=logging.getLogger("test"),
        invalidate_fn=lambda *a: None,
    )

    with mock.patch.dict(os.environ, {}, clear=True), \
         mock.patch("plugins.typesafe_jev.plugin.get_typesafe_api_key", return_value=None):
        plugin.on_post_sync(ctx)

    mock_client.search_threads.assert_not_called()


def test_typesafe_jev_uses_api_key_from_taxonomy(tmp_path: Path):
    """Verify on_post_sync picks up API_KEY from taxonomy.py when env var is absent."""
    tax_path = tmp_path / "taxonomy.py"
    tax_path.write_text(
        """
API_KEY = "taxonomy-configured-secret"
DOC_TYPE_CRITERIA = {"Statements": "Statements"}
DOMAIN_CRITERIA = {"Utilities": "Utilities"}
""",
        encoding="utf-8",
    )

    plugin = TypeSafeJevPlugin(taxonomy_path=str(tax_path), api_key=None)
    mock_client = mock.MagicMock()
    mock_client.search_threads.return_value = []
    ctx = PostSyncContext(
        stats={"new": 1},
        client=mock_client,
        logger=logging.getLogger("test"),
        invalidate_fn=lambda *a: None,
    )

    with mock.patch.dict(os.environ, {}, clear=True), \
         mock.patch("plugins.typesafe_jev.plugin.get_typesafe_api_key", return_value=None):
        plugin.on_post_sync(ctx)

    # Search was invoked because the key was loaded from taxonomy.py
    mock_client.search_threads.assert_called_once()


# =============================================================================
# Configuration & Validation Tests
# =============================================================================

def test_config_validation_post_sync_plugins():
    from ned.config import _validate_settings

    # Valid list
    with mock.patch.object(ned.settings, "post_sync_plugins", []):
        errors = _validate_settings()
        assert not any("post_sync_plugins" in e for e in errors)

    # Invalid type
    with mock.patch.object(ned.settings, "post_sync_plugins", "not-a-list"):
        errors = _validate_settings()
        assert any("post_sync_plugins must be a list" in e for e in errors)


def test_init_config_includes_plugins_section(tmp_path: Path):
    from ned.config import init_config

    cfg_path = tmp_path / "config.py"
    fake_plugin_dir = tmp_path / "plugins"
    fake_plugin_dir.mkdir()
    (fake_plugin_dir / "custom_tagger.py").write_text("# custom plugin\n")
    (fake_plugin_dir / "typesafe_jev").mkdir()

    mock_derived = {
        "mail_root": str(tmp_path / "Mail"),
        "smtp_accounts": ["personal"],
        "imap_accounts": ["personal"],
        "local_accounts": [],
        "email_address": {"personal": "me@example.com"},
        "sent_dir": {"personal": "personal/Sent"},
        "archive_dir": str(tmp_path / "Archive"),
    }

    with mock.patch("ned.config.config_path", return_value=str(cfg_path)), \
         mock.patch("ned.config._inspect_notmuch_and_maildir", return_value=mock_derived), \
         mock.patch("ned.config.init_rules", return_value=(str(tmp_path / "rules.py"), False)), \
         mock.patch("os.path.expanduser", side_effect=lambda p: str(fake_plugin_dir) if "plugins" in p else p), \
         mock.patch("ned.config.detect_tailscale", return_value={"ts_ip": None, "ts_dns": None, "serve_url": None}):
        path_written, _ = init_config()

    content = cfg_path.read_text()
    assert "SECTION 4: POST-SYNC PLUGINS" in content
    assert "Detected plugin files/directories" in content
    assert "custom_tagger" in content
    assert "typesafe_jev" in content


def test_typesafe_jev_batch_tagging(tmp_path: Path):
    """PERF-3: TypeSafeJevPlugin calls modify_tags_batch when supported."""
    tax_path = tmp_path / "taxonomy.py"
    tax_path.write_text(
        """
DOC_TYPE_CRITERIA = {"Statements": "Statements"}
DOMAIN_CRITERIA = {"Utilities": "Utilities"}
""",
        encoding="utf-8",
    )
    plugin = TypeSafeJevPlugin(
        taxonomy_path=str(tax_path),
        api_key="fake-key",
    )

    threads = [
        {"thread": "t1", "authors": "a@pge.com", "subject": "Bill", "tags": []},
        {"thread": "t2", "authors": "b@pge.com", "subject": "Bill 2", "tags": []},
    ]

    mock_client = mock.MagicMock(spec=["search_threads", "modify_tags_batch"])
    mock_client.search_threads.return_value = threads
    mock_client.modify_tags_batch.return_value = True

    ctx = PostSyncContext(
        stats={"new": 2},
        client=mock_client,
        logger=logging.getLogger("test"),
        invalidate_fn=lambda *a: None,
    )

    with mock.patch.object(
        plugin,
        "evaluate_email",
        return_value={
            "doc_type": "Statements",
            "doc_type_conf": 0.95,
            "domain": "Utilities",
            "domain_conf": 0.92,
        },
    ):
        plugin.on_post_sync(ctx)

    mock_client.modify_tags_batch.assert_called_once()
    call_args = mock_client.modify_tags_batch.call_args[0][0]
    assert len(call_args) == 2
    assert any("thread:t1" in line for line in call_args)
    assert any("thread:t2" in line for line in call_args)


def test_typesafe_api_key_cached():
    """PERF-3: get_typesafe_api_key caches result and avoids repeated shell subprocesses."""
    from plugins.typesafe_jev import plugin as pj

    with mock.patch.dict(os.environ, {}, clear=True), \
         mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(stdout="cached-key\n")
        pj._TYPESAFE_API_KEY_CHECKED = False
        pj._TYPESAFE_API_KEY_CACHE = None

        key1 = pj.get_typesafe_api_key()
        key2 = pj.get_typesafe_api_key()

        assert key1 == "cached-key"
        assert key2 == "cached-key"
        assert mock_run.call_count == 1


