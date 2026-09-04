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
"""Tests for Lazarus desktop client integration with NED daemon."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any, Optional, Set
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QModelIndex
from PyQt6.QtWidgets import QApplication
import pytest

from lazarus.actions import MarkableActionsMixin
from lazarus.client import ensure_daemon, get_client, is_ned_active, reset_client
from lazarus.controller import _NedEventBridge
from lazarus.ned.daemon import NedDaemon
from lazarus.ned.events import broadcaster
from lazarus.search import SearchModel
from lazarus.core import service


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def temp_ned_socket():
    with tempfile.TemporaryDirectory() as td:
        yield str(Path(td) / "desktop_ned.sock")


@pytest.fixture
def running_ned(temp_ned_socket):
    daemon = NedDaemon(
        socket_path=temp_ned_socket,
        enable_tcp=False,
    )
    daemon.start()
    for _ in range(50):
        if os.path.exists(temp_ned_socket):
            break
        time.sleep(0.02)
    old_sock = os.environ.get("NED_SOCK")
    old_disable = os.environ.get("LAZARUS_DISABLE_NED")
    os.environ["NED_SOCK"] = temp_ned_socket
    os.environ.pop("LAZARUS_DISABLE_NED", None)
    reset_client()
    yield daemon, temp_ned_socket
    daemon.stop()
    reset_client()
    if old_sock is not None:
        os.environ["NED_SOCK"] = old_sock
    else:
        os.environ.pop("NED_SOCK", None)
    if old_disable is not None:
        os.environ["LAZARUS_DISABLE_NED"] = old_disable
    else:
        os.environ.pop("LAZARUS_DISABLE_NED", None)


def test_client_singleton_and_disable():
    old = os.environ.get("LAZARUS_DISABLE_NED")
    try:
        reset_client()
        os.environ["LAZARUS_DISABLE_NED"] = "1"
        assert not is_ned_active()
    finally:
        if old is not None:
            os.environ["LAZARUS_DISABLE_NED"] = old
        else:
            os.environ.pop("LAZARUS_DISABLE_NED", None)
        reset_client()


def test_client_active_with_daemon(running_ned):
    assert is_ned_active()
    client = get_client()
    assert client.ping()


class DummyApp:
    def __init__(self):
        self.refreshed = False
        self.updated_thread: Optional[str] = None
        self.messages: list[tuple[str, str]] = []

    def refresh_panels(self):
        self.refreshed = True

    def update_single_thread(self, thread_id: str):
        self.updated_thread = thread_id

    def status_message(self, msg: str, kind: str = "info", duration: int = 3000):
        self.messages.append((msg, kind))


class DummySearchPanel(MarkableActionsMixin):
    def __init__(self, current_id="0000000000001234", tags=None, marked=False):
        self.app = DummyApp()
        self._curr_id = current_id
        self._tags = tags if tags is not None else {"inbox", "unread", "work"}
        self._marked = marked

    def _marked_query(self) -> str:
        return "tag:marked"

    def _has_marked_threads(self) -> bool:
        return self._marked

    def _current_thread_id(self) -> Optional[str]:
        return self._curr_id

    def _current_thread_tags(self) -> Optional[Set[str]]:
        return self._tags

    def _advance_selection(self) -> None:
        pass


def test_desktop_actions_via_ned(running_ned, monkeypatch):
    panel = DummySearchPanel(current_id="0000000000001234")

    # Mock service functions called by NED
    service_calls = []

    def mock_modify_tags(queries, add_tags, remove_tags):
        service_calls.append(("tag", queries, add_tags, remove_tags))
        return True

    def mock_archive(q):
        service_calls.append(("archive", q))
        return True

    def mock_trash(q):
        service_calls.append(("trash", q))
        return True

    def mock_untrash(q):
        service_calls.append(("untrash", q))
        return True

    monkeypatch.setattr(service, "modify_tags", mock_modify_tags)
    monkeypatch.setattr(service, "archive_thread", mock_archive)
    monkeypatch.setattr(service, "trash_thread", mock_trash)
    monkeypatch.setattr(service, "untrash_thread", mock_untrash)

    # Test single thread tag
    panel.tag_thread("+starred -unread")
    assert panel.app.updated_thread == "0000000000001234"
    assert len(service_calls) == 1
    assert service_calls[-1] == ("tag", ["thread:0000000000001234"], ["starred"], ["unread"])

    # Test archive single thread (tag-only: removes inbox, unread)
    panel.archive_thread()
    assert panel.app.updated_thread == "0000000000001234"
    assert len(service_calls) == 2
    assert service_calls[-1] == ("tag", ["thread:0000000000001234"], [], ["inbox", "unread"])

    # Test archive to local single thread (file move)
    panel.archive_to_local()
    assert panel.app.updated_thread == "0000000000001234"
    assert len(service_calls) == 3
    assert service_calls[-1] == ("archive", "0000000000001234")

    # Test trash single thread
    panel.delete_thread()
    assert panel.app.updated_thread == "0000000000001234"
    assert len(service_calls) == 4
    assert service_calls[-1] == ("trash", "0000000000001234")

    # Test untrash single thread
    panel.restore_thread_from_trash()
    assert panel.app.updated_thread == "0000000000001234"
    assert len(service_calls) == 5
    assert service_calls[-1] == ("untrash", "0000000000001234")

    # Test marked batch actions
    marked_panel = DummySearchPanel(marked=True)
    marked_panel.tag_thread("+reviewed", mode="tag marked")
    assert marked_panel.app.refreshed
    assert service_calls[-1] == ("tag", ["tag:marked"], ["reviewed"], [])

    marked_panel.archive_thread()
    assert service_calls[-1] == ("tag", ["tag:marked"], [], ["inbox", "unread"])

    marked_panel.archive_to_local()
    assert service_calls[-1] == ("archive", "tag:marked")

    marked_panel.delete_thread()
    assert service_calls[-1] == ("trash", "tag:marked")

    marked_panel.restore_thread_from_trash()
    assert service_calls[-1] == ("untrash", "tag:marked")


def test_desktop_search_model_via_ned(qapp, running_ned, monkeypatch):
    sample_threads = [
        {
            "thread": "0000000000001234",
            "timestamp": 1700000000,
            "date_relative": "Today",
            "matched": 1,
            "total": 1,
            "authors": "Alice",
            "subject": "Testing NED pure client",
            "tags": ["inbox", "unread"],
        }
    ]

    monkeypatch.setattr(service, "search_threads", lambda q, limit=50, offset=0: sample_threads)

    model = SearchModel(q="tag:inbox")
    assert model.num_threads == 1
    assert model.d[0]["subject"] == "Testing NED pure client"

    # Test refresh_thread
    model.refresh_thread("0000000000001234")
    assert model.num_threads == 1


def test_desktop_thread_model_via_ned(qapp, running_ned, monkeypatch):
    from lazarus.thread_model import ThreadModel

    sample_tree = [
        [
            {
                "id": "msg-123",
                "match": True,
                "excluded": False,
                "filename": ["/mail/cur/123"],
                "timestamp": 1700000000,
                "date_relative": "Today",
                "tags": ["inbox", "unread"],
                "headers": {
                    "Subject": "Hello from NED",
                    "From": "alice@example.com",
                    "To": "bob@example.com",
                    "Date": "2026-09-03",
                },
                "body": [{"id": 1, "content-type": "text/plain", "content": "Test body"}],
            },
            [],
        ]
    ]

    monkeypatch.setattr(
        service,
        "get_thread_messages",
        lambda tid, include_bodies=True: {
            "thread_id": tid,
            "subject": "Hello from NED",
            "tags": ["inbox", "unread"],
            "messages": [],
            "tree": [sample_tree],
        },
    )
    monkeypatch.setattr(service, "search_messages", lambda q, limit=1000, offset=0: ["msg-123"])

    model = ThreadModel("0000000000001234", "tag:inbox", mode="thread")
    model.refresh()
    assert model.raw_data is not None
    assert len(model.roots) == 1
    assert model.roots[0].msg["id"] == "msg-123"

    # Test tagging message via NED
    tag_calls = []
    monkeypatch.setattr(
        service, "modify_tags", lambda queries, add_tags, remove_tags: tag_calls.append((queries, add_tags, remove_tags)) or True
    )

    idx = model.index(0, 0, QModelIndex())
    model.tag_message(idx, "+starred -unread")
    assert len(tag_calls) == 1
    assert tag_calls[0] == (["id:msg-123"], ["starred"], ["unread"])
    assert "starred" in model.message_at(idx)["tags"]
    assert "unread" not in model.message_at(idx)["tags"]


def test_desktop_sse_bridge_and_cleanup(qapp, running_ned):
    bridge = _NedEventBridge()
    received_threads = []
    received_thread_id = []

    bridge.invalidate_threads.connect(lambda: received_threads.append(True))
    bridge.invalidate_thread.connect(lambda tid: received_thread_id.append(tid))

    client = get_client()
    stop_ev = threading.Event()

    def on_event(ev):
        if ev.scope == "threads":
            bridge.invalidate_threads.emit()
        elif ev.scope == "thread" and ev.target_id:
            bridge.invalidate_thread.emit(ev.target_id)

    watcher = client.watch_events(on_event=on_event, stop_event=stop_ev)
    time.sleep(0.1)

    broadcaster.broadcast_invalidate("threads", reason="sync")
    broadcaster.broadcast_invalidate("thread", "0000000000009999", reason="tag")

    # Process Qt event loop
    for _ in range(20):
        qapp.processEvents()
        if received_threads and received_thread_id:
            break
        time.sleep(0.05)

    assert len(received_threads) >= 1
    assert "0000000000009999" in received_thread_id

    # Test clean shutdown without blocking
    t0 = time.time()
    stop_ev.set()
    watcher.join(timeout=1.0)
    assert not watcher.is_alive()
    assert time.time() - t0 < 0.8


def test_ensure_daemon_spawn_command(monkeypatch, tmp_path):
    sock_file = str(tmp_path / "test_ned.sock")
    monkeypatch.setenv("NED_SOCK", sock_file)
    monkeypatch.delenv("LAZARUS_DISABLE_NED", raising=False)

    call_count = 0

    def mock_is_active(force=False):
        nonlocal call_count
        call_count += 1
        return call_count > 1

    monkeypatch.setattr("lazarus.client.is_ned_active", mock_is_active)

    with patch("lazarus.client.subprocess.Popen") as mock_popen:
        assert ensure_daemon(timeout=1.0) is True
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        assert cmd == [
            sys.executable,
            "-m",
            "lazarus.ned.main",
            f"--socket={sock_file}",
        ]
        assert kwargs.get("start_new_session") is True
        assert kwargs.get("close_fds") is True


def test_ensure_daemon_disabled(monkeypatch):
    monkeypatch.setenv("LAZARUS_DISABLE_NED", "1")
    with patch("lazarus.client.subprocess.Popen") as mock_popen:
        assert ensure_daemon() is False
        mock_popen.assert_not_called()

