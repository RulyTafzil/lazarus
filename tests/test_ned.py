"""Tests for the Notmuch Email Daemon (NED)."""

import http.client
import json
import os
import socket

import pytest

from ned.concurrency import MutationLock
from ned.daemon import NedDaemon
from ned.events import EventBroadcaster, broadcaster
from ned import settings


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


def test_ned_config_is_ned_only(tmp_path, monkeypatch):
    """NED reads ~/.config/ned/config.py only — never the desktop's lazarus config."""
    from ned.config import config_path, load_config, ConfigError

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    # Only the desktop config exists → NED must refuse to follow it.
    lazarus_dir = tmp_path / "lazarus"
    lazarus_dir.mkdir()
    (lazarus_dir / "config.py").write_text("import lazarus\nsettings.web_port = 8081\n")
    assert config_path() == str(tmp_path / "ned" / "config.py")
    with pytest.raises(ConfigError):
        load_config()

    # A ned config is found and loaded (and lazarus' value is ignored).
    ned_dir = tmp_path / "ned"
    ned_dir.mkdir()
    (ned_dir / "config.py").write_text(
        "import ned.settings as settings\n"
        "settings.email_address = 'Me <me@example.com>'\n"
        "settings.smtp_accounts = ['default']\n"
        "settings.sent_dir = '~/Mail/Sent'\n"
        "settings.web_port = 8082\n"
    )
    assert load_config() == str(ned_dir / "config.py")
    assert settings.web_port == 8082


def test_ned_accounts_and_signatures(tmp_path, monkeypatch):
    sock_path = str(tmp_path / "test_ned.sock")
    daemon = NedDaemon(socket_path=sock_path, enable_tcp=False)
    daemon.start()

    monkeypatch.setattr(settings, "smtp_accounts", ["work", "personal"])
    monkeypatch.setattr(settings, "use_signature", True)

    try:
        conn = UnixHTTPConnection(sock_path)

        # GET /api/v1/accounts returns accounts + per-account mail identity
        conn.request("GET", "/api/v1/accounts")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["accounts"] == ["work", "personal"]
        assert set(data["email"]) == {"work", "personal"}
        assert set(data["gnupg_keyid"]) == {"work", "personal"}

        # GET /api/accounts legacy alias keeps the same shape
        conn.request("GET", "/api/accounts")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["accounts"] == ["work", "personal"]
        assert set(data["email"]) == {"work", "personal"}
        assert set(data["gnupg_keyid"]) == {"work", "personal"}

        # GET /api/v1/signatures
        conn.request("GET", "/api/v1/signatures")
        resp = conn.getresponse()
        assert resp.status == 200
        sig_data = json.loads(resp.read().decode("utf-8"))
        assert sig_data["use_signature"] is True
        assert "signatures" in sig_data
        assert "work" in sig_data["signatures"]
        assert "personal" in sig_data["signatures"]

        conn.close()
    finally:
        daemon.stop()


def test_ned_get_part_attachment(tmp_path, monkeypatch):
    from ned import service

    sock_path = str(tmp_path / "test_ned.sock")
    daemon = NedDaemon(socket_path=sock_path, enable_tcp=False)
    daemon.start()

    monkeypatch.setattr(
        service,
        "get_part_data",
        lambda msg_id, part_id: (b"PDF_DATA_HERE", "application/pdf", "document.pdf"),
    )

    try:
        conn = UnixHTTPConnection(sock_path)
        conn.request("GET", "/api/v1/messages/msg-123/parts/2")
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "application/pdf"
        assert resp.getheader("Content-Disposition") == 'attachment; filename="document.pdf"'
        assert resp.read() == b"PDF_DATA_HERE"

        # Test part not found
        monkeypatch.setattr(
            service,
            "get_part_data",
            lambda msg_id, part_id: (b"", "application/octet-stream", "part-99"),
        )
        conn.request("GET", "/api/v1/messages/msg-123/parts/99")
        resp_404 = conn.getresponse()
        assert resp_404.status == 404
        conn.close()
    finally:
        daemon.stop()

def test_ned_include_html_on_thread_fetch(monkeypatch):
    """NED's thread fetch always requests HTML parts.

    The list-reply path used to miss ``--include-html`` and quote an empty
    body for HTML-only mail; that regression now lives in the daemon's
    fetch (``get_thread_messages``), so pin it at this layer.
    """
    from ned import service as core_service
    import ned.notmuch as nm

    calls: list = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        return type('R', (), {'stdout': '[]'})()

    monkeypatch.setattr(nm, 'run', fake_run)
    monkeypatch.setattr(core_service.notmuch, 'run', fake_run)

    try:
        core_service.get_thread_messages('thread:abc123')
    except Exception:
        pass  # empty DB output is fine; the arg contract is what we assert

    show_args = [a for a in calls if a and a[0] == 'show']
    assert show_args, 'get_thread_messages must invoke notmuch show'
    assert '--include-html' in show_args[0]
    assert '--decrypt=true' in show_args[0]


def test_ned_expunge_endpoint(tmp_path, monkeypatch):
    from ned import service

    sock_path = str(tmp_path / "test_ned.sock")
    daemon = NedDaemon(socket_path=sock_path, enable_tcp=False)
    daemon.start()

    monkeypatch.setattr(service, "expunge_trash", lambda: 3)

    try:
        conn = UnixHTTPConnection(sock_path)
        conn.request("POST", "/api/v1/expunge")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data == {"status": "ok", "tagged": 3, "ok": True}
        conn.close()
    finally:
        daemon.stop()


def test_ned_send_raw_mode(tmp_path, monkeypatch):
    """POST /api/v1/send with message_b64 pipes the raw RFC822 bytes to
    the account's command, saves a sent copy, and indexes."""
    from ned import service

    sock_path = str(tmp_path / "test_ned.sock")
    out = tmp_path / "out.eml"
    sent = tmp_path / "Sent"

    monkeypatch.setattr(service.settings, "smtp_accounts", ["default"])
    monkeypatch.setattr(service.settings, "email_address", "Me <me@example.com>")
    monkeypatch.setattr(
        service.settings, "send_mail_command", f"sh -c 'cat > {out}'")
    monkeypatch.setattr(service.settings, "sent_dir", str(sent))
    monkeypatch.setattr(service.notmuch, "new", lambda no_hooks=True: None)
    # A proper Maildir sent folder (cur/new/tmp), as mbsync would create.
    for sub in ("cur", "new", "tmp"):
        (sent / sub).mkdir(parents=True, exist_ok=True)

    daemon = NedDaemon(socket_path=sock_path, enable_tcp=False)
    daemon.start()
    try:
        import base64
        conn = UnixHTTPConnection(sock_path)
        raw = b"From: Me <me@example.com>\r\nTo: bob@example.com\r\nSubject: hi\r\n\r\nbody\r\n"
        payload = json.dumps({
            "account": "default",
            "message_b64": base64.b64encode(raw).decode("ascii"),
        })
        conn.request("POST", "/api/v1/send", body=payload,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data == {"status": "ok", "message": "Message sent successfully", "ok": True}

        # The raw bytes went through the send command untouched…
        assert out.read_bytes() == raw
        # …and a sent copy landed in the Maildir.
        assert list((sent / "new").iterdir()) or list((sent / "cur").iterdir())

        # Invalid base64 is rejected cleanly.
        bad = json.dumps({"account": "default", "message_b64": "not*base64!!"})
        conn.request("POST", "/api/v1/send", body=bad,
                     headers={"Content-Type": "application/json"})
        resp_bad = conn.getresponse()
        assert resp_bad.status == 400
        conn.close()
    finally:
        daemon.stop()
