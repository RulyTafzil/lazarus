"""Tests for the Notmuch Email Daemon (NED)."""

import http.client
import json
import os
from pathlib import Path
import socket
import time
import pytest

from lazarus.ned.concurrency import MutationLock, mutation_lock
from lazarus.ned.daemon import NedDaemon, get_default_socket_path
from lazarus.ned.events import EventBroadcaster, broadcaster
from lazarus import notmuch, settings


class UnixHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection over a local Unix domain stream socket."""

    def __init__(self, socket_path: str, timeout: float = 5.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def test_mutation_lock():
    lock = MutationLock()
    acquired = []

    def task(n: int) -> None:
        with lock:
            acquired.append(n)

    import threading
    t1 = threading.Thread(target=task, args=(1,))
    t2 = threading.Thread(target=task, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(acquired) == 2


def test_event_broadcaster():
    b = EventBroadcaster()
    q = b.subscribe()
    b.broadcast_invalidate("threads", reason="sync")

    msg = q.get(timeout=1.0)
    assert b"event: invalidate\n" in msg
    assert b'"scope": "threads"' in msg
    assert b'"reason": "sync"' in msg

    b.unsubscribe(q)


def test_ned_daemon_unix_socket(tmp_path, monkeypatch):
    sock_path = str(tmp_path / "test_ned.sock")
    daemon = NedDaemon(socket_path=sock_path, enable_tcp=False)
    daemon.start()

    try:
        assert os.path.exists(sock_path)

        # Connect via Unix socket HTTP
        conn = UnixHTTPConnection(sock_path)
        conn.request("GET", "/api/v1/tags")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert isinstance(data, list)
        conn.close()
    finally:
        daemon.stop()

    assert not os.path.exists(sock_path)


def test_ned_daemon_legacy_api_alias(tmp_path):
    sock_path = str(tmp_path / "test_ned.sock")
    daemon = NedDaemon(socket_path=sock_path, enable_tcp=False)
    daemon.start()

    try:
        conn = UnixHTTPConnection(sock_path)
        # Call legacy /api/tags
        conn.request("GET", "/api/tags")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert isinstance(data, list)
        conn.close()
    finally:
        daemon.stop()


def test_ned_daemon_tcp_and_auth(tmp_path):
    sock_path = str(tmp_path / "test_ned.sock")
    settings.web_token = "secret-test-token"
    settings.web_host = "127.0.0.1"

    # Find open port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    daemon = NedDaemon(
        socket_path=sock_path,
        tcp_host="127.0.0.1",
        tcp_port=port,
        enable_tcp=True,
    )
    daemon.start()

    try:
        # Request without token -> 401
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
        conn.request("GET", "/api/v1/tags")
        resp = conn.getresponse()
        assert resp.status == 401
        conn.close()

        # Request with Bearer token -> 200
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
        conn.request("GET", "/api/v1/tags", headers={"Authorization": "Bearer secret-test-token"})
        resp = conn.getresponse()
        assert resp.status == 200
        conn.close()
    finally:
        daemon.stop()
        settings.web_token = ""


def test_ned_sse_events(tmp_path):
    sock_path = str(tmp_path / "test_ned.sock")
    daemon = NedDaemon(socket_path=sock_path, enable_tcp=False)
    daemon.start()

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(sock_path)
        s.sendall(b"GET /api/v1/events HTTP/1.1\r\nHost: localhost\r\n\r\n")

        # Read initial response headers and connected comment
        buffer = b""
        while b"\n\n" not in buffer:
            chunk = s.recv(1024)
            if not chunk:
                break
            buffer += chunk

        assert b"text/event-stream" in buffer
        assert b": connected\n\n" in buffer

        # Broadcast an invalidation
        broadcaster.broadcast_invalidate("thread", "12345", "test")

        event_chunk = s.recv(1024)
        assert b"event: invalidate\n" in event_chunk
        assert b'"scope": "thread"' in event_chunk
        assert b'"id": "12345"' in event_chunk

        s.close()
    finally:
        daemon.stop()


def test_ned_config_cascade(tmp_path, monkeypatch):
    from lazarus.config import _config_path

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path, _ = _config_path(("ned", "lazarus"))
    assert path is None

    # Only lazarus exists -> loads lazarus
    lazarus_dir = tmp_path / "lazarus"
    lazarus_dir.mkdir()
    (lazarus_dir / "config.py").write_text("settings.web_port = 8081\n")
    path, _ = _config_path(("ned", "lazarus"))
    assert path == str(lazarus_dir / "config.py")

    # Both exist -> ned takes priority
    ned_dir = tmp_path / "ned"
    ned_dir.mkdir()
    (ned_dir / "config.py").write_text("settings.web_port = 8082\n")
    path, _ = _config_path(("ned", "lazarus"))
    assert path == str(ned_dir / "config.py")

