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
"""Unit tests for NedClient and NED client library."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from ned.client import (
    NedAuthenticationError,
    NedClient,
    NedConnectionError,
    NedError,
    NedEvent,
    NedNotFoundError,
    NedResponseError,
    main as client_main,
    resolve_default_socket_path,
)
from ned.daemon import NedDaemon
from ned.events import broadcaster


from ned import settings


@pytest.fixture
def temp_ned_socket():
    """Create a temporary socket path."""
    with tempfile.TemporaryDirectory() as td:
        yield str(Path(td) / "test_ned.sock")


@pytest.fixture
def running_ned_unix(temp_ned_socket):
    """Run a NedDaemon instance listening on a Unix domain socket for testing."""
    daemon = NedDaemon(
        socket_path=temp_ned_socket,
        enable_tcp=False,
    )
    daemon.start()
    # Wait until socket file exists
    for _ in range(50):
        if os.path.exists(temp_ned_socket):
            break
        time.sleep(0.02)
    yield daemon, temp_ned_socket
    daemon.stop()


@pytest.fixture
def running_ned_http():
    """Run a NedDaemon instance on localhost HTTP with a bearer token."""
    with tempfile.TemporaryDirectory() as td:
        sock_path = str(Path(td) / "test_ned.sock")
        old_token = settings.web_token
        old_host = settings.web_host
        settings.web_token = "test-secret-token"
        settings.web_host = "127.0.0.1"
        try:
            daemon = NedDaemon(
                socket_path=sock_path,
                enable_tcp=True,
                tcp_host="127.0.0.1",
                tcp_port=0,
            )
            daemon.start()
            assert daemon._tcp_server is not None
            port = daemon._tcp_server.server_port
            url = f"http://127.0.0.1:{port}"
            yield daemon, url, "test-secret-token"
            daemon.stop()
        finally:
            settings.web_token = old_token
            settings.web_host = old_host



# ---------------------------------------------------------------------------
# Client Configuration & Construction Tests
# ---------------------------------------------------------------------------


def test_client_init_defaults(monkeypatch):
    """Test default socket path resolution."""
    monkeypatch.delenv("NED_SOCK", raising=False)
    monkeypatch.delenv("NED_URL", raising=False)
    monkeypatch.delenv("NED_TOKEN", raising=False)

    client = NedClient()
    assert client.socket_path == resolve_default_socket_path()
    assert client.base_url is None
    assert client.token is None


def test_client_init_custom_socket():
    """Test custom socket path."""
    client = NedClient.unix("/tmp/custom.sock")
    assert client.socket_path == "/tmp/custom.sock"
    assert client.base_url is None


def test_client_init_http_url():
    """Test HTTP base URL and token."""
    client = NedClient.http("http://127.0.0.1:8080/", token="secret123")
    assert client.base_url == "http://127.0.0.1:8080"
    assert client.socket_path is None
    assert client.token == "secret123"


def test_client_init_unix_uri_schemes():
    """Test parsing unix:// and http+unix:// URIs."""
    client1 = NedClient(base_url="http+unix:///run/user/1000/ned.sock")
    assert client1.socket_path == "/run/user/1000/ned.sock"
    assert client1.base_url is None

    client2 = NedClient(base_url="unix:///run/user/1000/ned.sock")
    assert client2.socket_path == "/run/user/1000/ned.sock"
    assert client2.base_url is None


def test_client_init_env_variables(monkeypatch):
    """Test environment variable overrides."""
    monkeypatch.setenv("NED_SOCK", "/tmp/env.sock")
    monkeypatch.setenv("NED_TOKEN", "env-token")
    client = NedClient()
    assert client.socket_path == "/tmp/env.sock"
    assert client.token == "env-token"

    monkeypatch.delenv("NED_SOCK")
    monkeypatch.setenv("NED_URL", "http://env-host:9999")
    client2 = NedClient()
    assert client2.base_url == "http://env-host:9999"


def test_client_context_manager():
    """Test context manager protocol."""
    with NedClient.unix("/tmp/dummy.sock") as client:
        assert isinstance(client, NedClient)


# ---------------------------------------------------------------------------
# Connection & Error Tests
# ---------------------------------------------------------------------------


def test_client_connection_error():
    """Test error when daemon socket does not exist."""
    client = NedClient.unix("/nonexistent/path/to/ned.sock", timeout=1.0)
    assert not client.ping()
    with pytest.raises(NedConnectionError):
        client.search("tag:inbox")


def test_client_http_auth_failure(running_ned_http):
    """Test HTTP 401 raises NedAuthenticationError."""
    _, url, _ = running_ned_http

    # Connect without token
    client_no_token = NedClient.http(url, token=None)
    with pytest.raises(NedAuthenticationError) as exc_info:
        client_no_token.get_tags()
    assert exc_info.value.status == 401

    # Connect with invalid token
    client_bad_token = NedClient.http(url, token="wrong-token")
    with pytest.raises(NedAuthenticationError) as exc_info:
        client_bad_token.get_tags()
    assert exc_info.value.status == 401

    # Connect with correct token succeeds
    client_valid = NedClient.http(url, token="test-secret-token")
    assert client_valid.ping()
    assert isinstance(client_valid.health(), dict)


# ---------------------------------------------------------------------------
# End-to-End Tests over Unix Socket
# ---------------------------------------------------------------------------


def test_ping_and_health_unix(running_ned_unix):
    """Test ping and health endpoints over Unix socket."""
    _, sock_path = running_ned_unix
    client = NedClient.unix(sock_path)

    assert client.ping() is True
    health = client.health()
    assert health.get("status") == "ok"
    assert health.get("service") == "ned"


@patch("ned.service.search_threads")
def test_search(mock_search, running_ned_unix):
    """Test search endpoint returns thread list."""
    _, sock_path = running_ned_unix
    mock_search.return_value = [
        {"thread": "thread-1", "subject": "Hello", "authors": "Alice"},
        {"thread": "thread-2", "subject": "World", "authors": "Bob"},
    ]

    client = NedClient.unix(sock_path)
    res = client.search("tag:inbox", limit=10, offset=0)
    assert len(res) == 2
    assert res[0]["thread"] == "thread-1"
    mock_search.assert_called_with("tag:inbox", limit=10, offset=0)


@patch("ned.service.get_thread_messages")
def test_get_thread(mock_get_thread, running_ned_unix):
    """Test get_thread returns thread data or 404."""
    _, sock_path = running_ned_unix
    mock_get_thread.return_value = {
        "thread_id": "thread-123",
        "subject": "Test Thread",
        "messages": [],
    }

    client = NedClient.unix(sock_path)
    thread = client.get_thread("thread-123")
    assert thread["thread_id"] == "thread-123"

    mock_get_thread.return_value = None
    with pytest.raises(NedNotFoundError) as exc_info:
        client.get_thread("missing-thread")
    assert exc_info.value.status == 404


@patch("ned.service.get_part_data")
def test_get_part_and_part_data(mock_get_part, running_ned_unix):
    """Test downloading message parts and attachments."""
    _, sock_path = running_ned_unix
    mock_get_part.return_value = (b"Attachment content", "application/pdf", "document.pdf")

    client = NedClient.unix(sock_path)
    part_bytes = client.get_part("msg-456", 2)
    assert part_bytes == b"Attachment content"

    payload, ctype, filename = client.get_part_data("msg-456", 2)
    assert payload == b"Attachment content"
    assert ctype == "application/pdf"
    assert filename == "document.pdf"


@patch("ned.service.modify_tags")
def test_modify_tags(mock_tags, running_ned_unix):
    """Test modifying tags via queries, threads, or messages."""
    _, sock_path = running_ned_unix
    mock_tags.return_value = True

    client = NedClient.unix(sock_path)
    ok = client.modify_tags("thread:123", add=["unread"], remove=["inbox"])
    assert ok is True
    mock_tags.assert_called_with(
        queries=["thread:123"],
        threads=[],
        messages=[],
        add_tags=["unread"],
        remove_tags=["inbox"],
    )

    # Test explicit threads and messages lists
    ok = client.modify_tags(threads=["t1"], messages=["m1"], add=["reviewed"])
    assert ok is True
    mock_tags.assert_called_with(
        queries=[],
        threads=["t1"],
        messages=["m1"],
        add_tags=["reviewed"],
        remove_tags=[],
    )


@patch("ned.service.modify_tags")
def test_modify_thread_and_message_tags(mock_tags, running_ned_unix):
    """Test dedicated single thread and single message tag endpoints."""
    _, sock_path = running_ned_unix
    mock_tags.return_value = True

    client = NedClient.unix(sock_path)
    ok = client.modify_thread_tags("thread:t100", add=["flagged"], remove=["unread"])
    assert ok is True
    mock_tags.assert_called_with(
        threads=["t100"],
        add_tags=["flagged"],
        remove_tags=["unread"],
    )

    ok = client.modify_message_tags("msg<id1>@host", add=["replied"], remove=[])
    assert ok is True
    mock_tags.assert_called_with(
        messages=["msg<id1>@host"],
        add_tags=["replied"],
        remove_tags=[],
    )


@patch("ned.service.archive_thread")
@patch("ned.service.unarchive_thread")
@patch("ned.service.trash_thread")
@patch("ned.service.untrash_thread")
@patch("ned.service.toggle_flag")
def test_thread_actions(
    mock_star, mock_untrash, mock_trash, mock_unarchive, mock_archive, running_ned_unix
):
    """Test thread action endpoints (archive, trash, flag)."""
    _, sock_path = running_ned_unix
    mock_archive.return_value = True
    mock_unarchive.return_value = True
    mock_trash.return_value = True
    mock_untrash.return_value = True
    mock_star.return_value = True

    client = NedClient.unix(sock_path)

    assert client.archive_thread("t1") is True
    mock_archive.assert_called_with("t1")

    assert client.unarchive_thread("t1") is True
    mock_unarchive.assert_called_with("t1")

    assert client.trash_thread("t1") is True
    mock_trash.assert_called_with("t1")

    assert client.untrash_thread("t1") is True
    mock_untrash.assert_called_with("t1")

    assert client.toggle_flag("t1", flag=True) is True
    assert client.star_thread("t1", star=False) is True
    mock_star.assert_called_with("t1", flag=False)


@patch("ned.service.get_all_tags")
@patch("ned.service.get_contacts")
@patch("ned.service.get_reply_seed")
@patch("ned.service.get_signatures")
def test_metadata_queries(
    mock_sigs, mock_reply, mock_contacts, mock_tags, running_ned_unix
):
    """Test metadata queries (tags, contacts, reply seed, signatures, accounts)."""
    _, sock_path = running_ned_unix
    mock_tags.return_value = [{"tag": "inbox", "count": 10}]
    mock_contacts.return_value = [{"name": "Alice", "address": "alice@example.com", "display": "Alice <alice@example.com>"}]
    mock_reply.return_value = {"to": "alice@example.com", "subject": "Re: Hello", "body": "> text"}
    mock_sigs.return_value = {"use_signature": True, "signatures": {"default": "-- \nBest"}}

    client = NedClient.unix(sock_path)

    tags = client.get_tags()
    assert tags == [{"tag": "inbox", "count": 10}]

    contacts = client.get_contacts("alice")
    assert len(contacts) == 1
    assert contacts[0]["address"] == "alice@example.com"

    reply = client.get_reply_seed("msg-1", to_all=True)
    assert reply["subject"] == "Re: Hello"

    sigs = client.get_signatures()
    assert sigs == {"default": "-- \nBest"}

    accounts = client.get_accounts()
    assert isinstance(accounts, list)


@patch("ned.service.send_email")
def test_send_email_json_and_multipart(mock_send, running_ned_unix):
    """Test sending plain email via JSON and email with attachments via multipart."""
    _, sock_path = running_ned_unix
    mock_send.return_value = (True, "Message delivered")

    client = NedClient.unix(sock_path)

    # 1. Plain text send
    ok, msg = client.send_email(
        account="default",
        to=["bob@example.com"],
        subject="Test subject",
        body_text="Test body",
    )
    assert ok is True
    assert msg == "Message delivered"
    assert mock_send.call_args[1]["account"] == "default"
    assert mock_send.call_args[1]["to"] == ["bob@example.com"]
    assert mock_send.call_args[1]["attachments"] == []

    # 2. Send with attachments
    attachments = [("file.txt", "text/plain", b"Sample data")]
    ok2, msg2 = client.send_email(
        account="default",
        to="carol@example.com",
        subject="With attachment",
        body_text="See attached",
        attachments=attachments,
    )
    assert ok2 is True
    called_att = mock_send.call_args[1]["attachments"]
    assert len(called_att) == 1
    assert called_att[0][0] == "file.txt"
    assert called_att[0][1] == "text/plain"
    assert called_att[0][2] == b"Sample data"


@patch("ned.service.sync_mail")
def test_sync_mail(mock_sync, running_ned_unix):
    """Test mail synchronization."""
    _, sock_path = running_ned_unix
    mock_sync.return_value = (True, "3 new messages")

    client = NedClient.unix(sock_path)
    ok, msg = client.sync_mail()
    assert ok is True
    assert msg == "3 new messages"


# ---------------------------------------------------------------------------
# SSE Events Stream Tests
# ---------------------------------------------------------------------------


def test_sse_events_stream(running_ned_unix):
    """Test consuming SSE invalidation events from NedClient."""
    _, sock_path = running_ned_unix
    client = NedClient.unix(sock_path)

    received_events: list[NedEvent] = []
    stop_ev = threading.Event()

    def listener():
        for ev in client.listen_events(stop_event=stop_ev):
            received_events.append(ev)
            if len(received_events) >= 2:
                break

    t = threading.Thread(target=listener, daemon=True)
    t.start()

    try:
        time.sleep(0.1)
        # Broadcast events
        broadcaster.broadcast_invalidate("threads", reason="sync")
        time.sleep(0.05)
        broadcaster.broadcast_invalidate("thread", item_id="thread-999", reason="tag")

        t.join(timeout=3.0)
        assert len(received_events) == 2
        assert received_events[0].scope == "threads"
        assert received_events[0].reason == "sync"
        assert received_events[1].scope == "thread"
        assert received_events[1].target_id == "thread-999"
        assert received_events[1].reason == "tag"
    finally:
        stop_ev.set()
        t.join(timeout=2.0)


def test_watch_events_background_thread(running_ned_unix):
    """Test watch_events callback in background thread."""
    _, sock_path = running_ned_unix
    client = NedClient.unix(sock_path)

    collected: list[NedEvent] = []
    stop_ev = threading.Event()

    watcher_thread = client.watch_events(
        on_event=lambda ev: collected.append(ev),
        stop_event=stop_ev,
        reconnect=False,
    )
    assert watcher_thread.is_alive()

    try:
        time.sleep(0.1)
        broadcaster.broadcast_invalidate("threads", reason="archive")

        for _ in range(50):
            if len(collected) >= 1:
                break
            time.sleep(0.02)

        assert len(collected) >= 1
        assert collected[0].scope == "threads"
        assert collected[0].reason == "archive"
    finally:
        stop_ev.set()
        watcher_thread.join(timeout=2.0)
        assert not watcher_thread.is_alive()


# ---------------------------------------------------------------------------
# CLI Tests
# ---------------------------------------------------------------------------


def test_cli_ping_and_health(running_ned_unix, capsys):
    """Test CLI ping and health commands."""
    _, sock_path = running_ned_unix

    code = client_main(["--socket", sock_path, "ping"])
    assert code == 0
    out, _ = capsys.readouterr()
    assert "NED is reachable" in out

    code = client_main(["--socket", sock_path, "health"])
    assert code == 0
    out, _ = capsys.readouterr()
    assert '"status": "ok"' in out


@patch("ned.service.get_all_tags")
def test_cli_tags(mock_tags, running_ned_unix, capsys):
    """Test CLI tags command."""
    _, sock_path = running_ned_unix
    mock_tags.return_value = [{"tag": "inbox", "count": 5}]

    code = client_main(["--socket", sock_path, "tags"])
    assert code == 0
    out, _ = capsys.readouterr()
    assert '"tag": "inbox"' in out
