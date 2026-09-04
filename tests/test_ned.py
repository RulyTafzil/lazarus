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


def test_ned_openapi_and_canonical_routes(tmp_path, monkeypatch):
    """Canonical endpoint names respond; removed aliases 404; the live
    OpenAPI spec is served and lists the canonical paths."""
    from ned import service

    sock_path = str(tmp_path / "test_ned.sock")
    daemon = NedDaemon(socket_path=sock_path, enable_tcp=False)
    daemon.start()
    monkeypatch.setattr(service, "search_threads", lambda q, limit=50, offset=0: [])
    try:
        conn = UnixHTTPConnection(sock_path)

        # Live spec
        conn.request("GET", "/api/v1/openapi.json")
        resp = conn.getresponse()
        assert resp.status == 200
        spec = json.loads(resp.read().decode("utf-8"))
        assert spec["openapi"].startswith("3.0")
        assert "/api/v1/threads" in spec["paths"]
        assert "/api/v1/messages/{id}/parts/{part_id}" in spec["paths"]
        assert "/api/v1/threads/{id}/star" in spec["paths"]

        # Canonical names respond
        conn.request("GET", "/api/v1/threads?q=tag:inbox")
        assert conn.getresponse().status == 200
        conn.request("POST", "/api/v1/tags", json.dumps({"queries": ["tag:x"], "add": ["y"]}),
                     {"Content-Type": "application/json"})
        assert conn.getresponse().status == 200
        conn.request("POST", "/api/v1/threads/abc/star", json.dumps({"flag": True}),
                     {"Content-Type": "application/json"})
        assert conn.getresponse().status == 200

        # The removed aliases now 404 (canonical names only)
        for path in ("/api/v1/search?q=tag:inbox", "/api/v1/tag",
                     "/api/v1/threads/abc/flag",
                     "/api/v1/reply-seed?id=abc"):
            conn.request("GET" if path.startswith(("/api/v1/search", "/api/v1/reply-seed")) else "POST",
                         path, b"" if path == "/api/v1/tag" else None)
            assert conn.getresponse().status == 404, path
        conn.close()
    finally:
        daemon.stop()


def test_modify_tags_query_handling(monkeypatch):
    """modify_tags preserves queries as raw Notmuch queries and explicitly formats threads and messages."""
    import subprocess
    from ned import notmuch, service

    captured: list[tuple[str, str]] = []

    def mock_tag(tag_expr: str, query: str) -> subprocess.CompletedProcess:
        captured.append((tag_expr, query))
        return subprocess.CompletedProcess(args=["notmuch", "tag"], returncode=0)

    monkeypatch.setattr(notmuch, "tag", mock_tag)

    # Marked batch query (regression test for t m)
    service.modify_tags(queries=["tag:marked AND (tag:inbox)"], add_tags=["work"], remove_tags=["marked"])
    assert captured[-1] == ("+work -marked", "tag:marked AND (tag:inbox)")

    # Single tag query
    service.modify_tags(queries=["tag:unread"], add_tags=["todo"], remove_tags=[])
    assert captured[-1] == ("+todo", "tag:unread")

    # Wildcard query
    service.modify_tags(queries=["*"], add_tags=["archive"], remove_tags=[])
    assert captured[-1] == ("+archive", "*")

    # Multiple queries joined with or
    service.modify_tags(queries=["tag:marked", "tag:todo"], add_tags=["urgent"], remove_tags=[])
    assert captured[-1] == ("+urgent", "tag:marked or tag:todo")

    # Explicit threads
    service.modify_tags(threads=["0000000000001234"], add_tags=["flagged"], remove_tags=[])
    assert captured[-1] == ("+flagged", "thread:0000000000001234")

    # Already prefixed thread query in threads list
    service.modify_tags(threads=["thread:0000000000001234"], add_tags=["flagged"], remove_tags=[])
    assert captured[-1] == ("+flagged", "thread:0000000000001234")

    # Explicit messages (raw and angle-bracketed)
    service.modify_tags(messages=["abc@example.com"], add_tags=["replied"], remove_tags=[])
    assert captured[-1] == ("+replied", "id:abc@example.com")

    service.modify_tags(messages=["<abc@example.com>"], add_tags=["replied"], remove_tags=[])
    assert captured[-1] == ("+replied", "id:abc@example.com")

    # Legacy ids compatibility parameter
    service.modify_tags(ids=["thread:0000000000001234", "<msg1@host>"], add_tags=["read"], remove_tags=[])
    assert captured[-1] == ("+read", "thread:0000000000001234 or id:msg1@host")


def test_ned_tag_endpoints(tmp_path, monkeypatch):
    """Test POST /api/v1/tags, POST /api/v1/threads/{id}/tags, and POST /api/v1/messages/{id}/tags."""
    from ned import service

    sock_path = str(tmp_path / "test_ned.sock")
    daemon = NedDaemon(socket_path=sock_path, enable_tcp=False)
    daemon.start()

    calls: list[dict] = []

    def mock_service_modify(queries=(), add_tags=(), remove_tags=(), *, threads=(), messages=(), ids=()):
        calls.append({
            "queries": list(queries),
            "threads": list(threads),
            "messages": list(messages),
            "ids": list(ids),
            "add": list(add_tags),
            "remove": list(remove_tags),
        })
        return True

    monkeypatch.setattr(service, "modify_tags", mock_service_modify)

    try:
        conn = UnixHTTPConnection(sock_path)

        # 1. POST /api/v1/tags with queries, threads, and messages
        payload = json.dumps({
            "queries": ["tag:unread"],
            "threads": ["0000000000001234"],
            "messages": ["m1@example.com"],
            "add": ["reviewed"],
            "remove": ["unread"],
        })
        conn.request("POST", "/api/v1/tags", body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 200
        assert json.loads(resp.read().decode("utf-8")) == {"status": "ok", "ok": True}
        assert calls[-1] == {
            "queries": ["tag:unread"],
            "threads": ["0000000000001234"],
            "messages": ["m1@example.com"],
            "ids": [],
            "add": ["reviewed"],
            "remove": ["unread"],
        }

        # 2. POST /api/v1/threads/{id}/tags
        t_payload = json.dumps({"add": ["flagged"], "remove": ["unread"]})
        conn.request("POST", "/api/v1/threads/0000000000001234/tags", body=t_payload,
                     headers={"Content-Type": "application/json"})
        resp2 = conn.getresponse()
        assert resp2.status == 200
        assert json.loads(resp2.read().decode("utf-8")) == {"status": "ok", "ok": True}
        assert calls[-1]["threads"] == ["0000000000001234"]
        assert calls[-1]["add"] == ["flagged"]
        assert calls[-1]["remove"] == ["unread"]

        # 3. POST /api/v1/messages/{id}/tags
        m_payload = json.dumps({"add": ["replied"], "remove": []})
        conn.request("POST", "/api/v1/messages/msg-123/tags", body=m_payload,
                     headers={"Content-Type": "application/json"})
        resp3 = conn.getresponse()
        assert resp3.status == 200
        assert json.loads(resp3.read().decode("utf-8")) == {"status": "ok", "ok": True}
        assert calls[-1]["messages"] == ["msg-123"]
        assert calls[-1]["add"] == ["replied"]
        assert calls[-1]["remove"] == []

        # 4. Error response when missing tags
        conn.request("POST", "/api/v1/threads/t1/tags", body=json.dumps({}),
                     headers={"Content-Type": "application/json"})
        resp_err = conn.getresponse()
        assert resp_err.status == 400

        conn.close()
    finally:
        daemon.stop()


def test_service_maildir_move_actions(monkeypatch):
    """Test trash, restore, and archive_local in ned.service with unmark flag."""
    from ned import notmuch, service, actions

    tagged: list[tuple[str, str]] = []
    trash_moves: list[tuple[str, bool]] = []
    restore_moves: list[tuple[str, bool]] = []
    archive_moves: list[tuple[str, bool]] = []

    monkeypatch.setattr(actions, "move_to_trash", lambda q, unmark=False: (trash_moves.append((q, unmark)), 1)[1])
    monkeypatch.setattr(actions, "restore_from_trash", lambda q, unmark=False: (restore_moves.append((q, unmark)), 1)[1])
    monkeypatch.setattr(actions, "move_to_archive", lambda q, unmark=False: (archive_moves.append((q, unmark)), 1)[1])

    # 1. trash with unmark
    service.trash(queries=["tag:marked"], unmark=True)
    assert trash_moves[-1] == ("tag:marked", True)

    # 2. trash single thread without unmark
    service.trash(threads=["0000000000001234"])
    assert trash_moves[-1] == ("thread:0000000000001234", False)

    # 3. restore with unmark
    service.restore(queries=["tag:marked"], unmark=True)
    assert restore_moves[-1] == ("tag:trash AND (tag:marked)", True)

    # 4. restore single message
    service.restore(messages=["<msg-1@host>"])
    assert restore_moves[-1] == ("tag:trash AND (id:msg-1@host)", False)

    # 5. archive_local with unmark
    service.archive_local(queries=["tag:marked"], unmark=True)
    assert archive_moves[-1] == ("tag:marked", True)


def test_ned_maildir_move_endpoints(tmp_path, monkeypatch):
    """Test POST /api/v1/trash, restore, move-archive for batch, threads, and messages."""
    from ned import service

    sock_path = str(tmp_path / "test_ned.sock")
    daemon = NedDaemon(socket_path=sock_path, enable_tcp=False)
    daemon.start()

    calls: list[dict] = []

    def mock_trash(queries=(), *, threads=(), messages=(), ids=(), unmark=False):
        calls.append({"action": "trash", "queries": list(queries), "threads": list(threads), "messages": list(messages), "unmark": unmark})
        return True

    def mock_restore(queries=(), *, threads=(), messages=(), ids=(), unmark=False):
        calls.append({"action": "restore", "queries": list(queries), "threads": list(threads), "messages": list(messages), "unmark": unmark})
        return True

    def mock_archive_local(queries=(), *, threads=(), messages=(), ids=(), unmark=False):
        calls.append({"action": "archive_local", "queries": list(queries), "threads": list(threads), "messages": list(messages), "unmark": unmark})
        return True

    monkeypatch.setattr(service, "trash", mock_trash)
    monkeypatch.setattr(service, "restore", mock_restore)
    monkeypatch.setattr(service, "archive_local", mock_archive_local)

    try:
        conn = UnixHTTPConnection(sock_path)

        # Batch trash
        b_payload = json.dumps({"queries": ["tag:marked"], "unmark": True})
        conn.request("POST", "/api/v1/trash", body=b_payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 200
        assert calls[-1] == {"action": "trash", "queries": ["tag:marked"], "threads": [], "messages": [], "unmark": True}

        # Batch restore
        conn.request("POST", "/api/v1/restore", body=b_payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 200
        assert calls[-1] == {"action": "restore", "queries": ["tag:marked"], "threads": [], "messages": [], "unmark": True}

        # Batch move-archive
        conn.request("POST", "/api/v1/move-archive", body=b_payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 200
        assert calls[-1] == {"action": "archive_local", "queries": ["tag:marked"], "threads": [], "messages": [], "unmark": True}

        # Single thread trash
        conn.request("POST", "/api/v1/threads/0000000000001234/trash")
        resp = conn.getresponse()
        assert resp.status == 200
        assert calls[-1] == {"action": "trash", "queries": [], "threads": ["0000000000001234"], "messages": [], "unmark": False}

        # Single thread restore
        conn.request("POST", "/api/v1/threads/0000000000001234/restore")
        resp = conn.getresponse()
        assert resp.status == 200
        assert calls[-1] == {"action": "restore", "queries": [], "threads": ["0000000000001234"], "messages": [], "unmark": False}

        # Single thread move-archive
        conn.request("POST", "/api/v1/threads/0000000000001234/move-archive")
        resp = conn.getresponse()
        assert resp.status == 200
        assert calls[-1] == {"action": "archive_local", "queries": [], "threads": ["0000000000001234"], "messages": [], "unmark": False}

        # Message trash with thread_id query param
        conn.request("POST", "/api/v1/messages/m1%40example.com/trash?thread_id=0000000000001234")
        resp = conn.getresponse()
        assert resp.status == 200
        assert calls[-1] == {"action": "trash", "queries": [], "threads": [], "messages": ["m1@example.com"], "unmark": False}

        # Message restore
        conn.request("POST", "/api/v1/messages/m1%40example.com/restore?thread_id=0000000000001234")
        resp = conn.getresponse()
        assert resp.status == 200
        assert calls[-1] == {"action": "restore", "queries": [], "threads": [], "messages": ["m1@example.com"], "unmark": False}

        # Message move-archive
        conn.request("POST", "/api/v1/messages/m1%40example.com/move-archive?thread_id=0000000000001234")
        resp = conn.getresponse()
        assert resp.status == 200
        assert calls[-1] == {"action": "archive_local", "queries": [], "threads": [], "messages": ["m1@example.com"], "unmark": False}

        conn.close()
    finally:
        daemon.stop()

