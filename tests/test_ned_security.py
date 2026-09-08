"""TCP bind security guard — unauthenticated non-loopback/Tailscale refusal."""
import os
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ned.daemon import insecure_tcp_error, is_loopback_host, is_tailscale_host
from ned.handler import NedRequestHandler


# -- pure guard logic --------------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_always_allowed(host):
    assert is_loopback_host(host)
    assert insecure_tcp_error(host, "") is None


@pytest.mark.parametrize("host", ["100.64.0.0", "100.100.1.2", "100.127.255.255"])
def test_tailscale_always_allowed(host):
    assert is_tailscale_host(host)
    assert insecure_tcp_error(host, "") is None


@pytest.mark.parametrize("host", ["192.168.1.50", "10.0.0.5", "172.16.3.4", "0.0.0.0"])
def test_lan_bind_without_token_refused(host):
    err = insecure_tcp_error(host, "")
    assert err is not None
    assert "Refusing" in err
    assert host in err


def test_token_allows_lan_bind():
    assert insecure_tcp_error("192.168.1.50", "secret") is None


def test_allow_insecure_overrides():
    err = insecure_tcp_error("192.168.1.50", "", allow_insecure=True)
    assert err is None


def test_empty_host_defaults_safe():
    assert insecure_tcp_error("", "") is None


# -- CLI behavior --------------------------------------------------------------

def _run_ned(sock_dir, *extra, timeout=5):
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace
    sock = str(Path(tempfile.mkdtemp(dir=sock_dir)) / "ned.sock")
    try:
        return subprocess.run(
            [sys.executable, "-m", "ned.main", f"--socket={sock}", *extra],
            capture_output=True, text=True, timeout=timeout, cwd=os.getcwd())
    except subprocess.TimeoutExpired as e:
        # Daemon started and is serving (expected for allowed binds).
        return SimpleNamespace(returncode=None,
                               stderr=(e.stderr or "") if isinstance(e.stderr, str) else "",
                               stdout=(e.stdout or "") if isinstance(e.stdout, str) else "")


def test_cli_refuses_insecure_lan_bind(tmp_path):
    """ned --host <lan-ip> without a token exits 2 before binding."""
    res = _run_ned(str(tmp_path), "--host", "192.168.1.50")
    assert isinstance(res, subprocess.CompletedProcess)
    assert res.returncode == 2
    assert "Refusing to expose NED unauthenticated on 192.168.1.50" in res.stderr
    assert "--allow-insecure" in res.stderr


def test_cli_allow_insecure_accepts_lan_bind(tmp_path):
    """--allow-insecure disables the guard (daemon starts and serves)."""
    res = _run_ned(str(tmp_path), "--host", "192.168.1.50", "--allow-insecure")
    assert res.returncode is None  # still running = accepted


def test_cli_tailscale_bind_not_refused(tmp_path):
    """A Tailscale host with no token passes the guard (WireGuard transport)."""
    res = _run_ned(str(tmp_path), "--host", "100.80.0.1")
    assert "Refusing to expose NED unauthenticated" not in res.stderr


def test_cli_token_allows_lan_bind(tmp_path):
    """Setting a token permits a LAN bind without --allow-insecure."""
    res = _run_ned(str(tmp_path), "--host", "192.168.1.50", "--token", "secret")
    assert "Refusing to expose NED unauthenticated" not in res.stderr


# -- request-level CSRF / DNS-rebinding guard ----------------------------------


def _raw_http(port: int, request: bytes) -> tuple[int, bytes]:
    """Send a raw HTTP/1.1 request and return (status, full response body)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(("127.0.0.1", port))
        s.sendall(request)
        buffer = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buffer += chunk
        status_line = buffer.split(b"\r\n", 1)[0]
        return int(status_line.split(b" ")[1]), buffer


def _get(port: int, host: str = "127.0.0.1", origin: str | None = None,
         path: str = "/api/v1/ping", token: str | None = None) -> tuple[int, bytes]:
    req = f"GET {path} HTTP/1.1\r\nHost: {host}\r\n".encode()
    if origin is not None:
        req += f"Origin: {origin}\r\n".encode()
    if token is not None:
        req += f"Authorization: Bearer {token}\r\n".encode()
    req += b"Connection: close\r\n\r\n"
    return _raw_http(port, req)


@pytest.fixture()
def tcp_daemon(tmp_path):
    """NedDaemon on 127.0.0.1/port 0 with no token (loopback-only)."""
    from ned import settings
    from ned.daemon import NedDaemon
    old_host = settings.web_host
    old_token = settings.web_token
    settings.web_host = "127.0.0.1"
    settings.web_token = ""
    daemon = NedDaemon(
        socket_path=str(tmp_path / "guard.sock"),
        enable_tcp=True,
        tcp_host="127.0.0.1",
        tcp_port=0,
    )
    daemon.start()
    try:
        assert daemon._tcp_server is not None
        yield daemon._tcp_server.server_port
    finally:
        daemon.stop()
        settings.web_host = old_host
        settings.web_token = old_token


def test_tcp_missing_host_header_rejected(tcp_daemon):
    status, body = _raw_http(tcp_daemon, b"GET /api/v1/ping HTTP/1.1\r\n\r\n")
    assert status == 400
    assert b"Missing Host header" in body


def test_tcp_foreign_host_rejected(tcp_daemon):
    """DNS rebinding: an arbitrary hostname must not reach the listener."""
    status, body = _get(tcp_daemon, host="evil.example")
    assert status == 403
    assert b"Forbidden host header" in body


def test_tcp_loopback_host_allowed(tcp_daemon):
    for host in ("127.0.0.1", "localhost", "::1"):
        status, _ = _get(tcp_daemon, host=host)
        assert status == 200, host


def test_tcp_cross_origin_rejected(tcp_daemon):
    """CSRF: a third-party website's Origin must not drive requests."""
    status, body = _get(tcp_daemon, origin="http://evil.example")
    assert status == 403
    assert b"Cross-origin request rejected" in body


def test_tcp_null_origin_rejected(tcp_daemon):
    status, body = _get(tcp_daemon, origin="null")
    assert status == 403
    assert b"Cross-origin request rejected" in body


def test_tcp_same_origin_allowed(tcp_daemon):
    """PWA-style same-origin requests pass (matching host + port)."""
    origin = f"http://127.0.0.1:{tcp_daemon}"
    status, _ = _get(tcp_daemon, origin=origin)
    assert status == 200


def test_tcp_non_browser_client_allowed(tcp_daemon):
    """Desktop / ned-client / ned-mcp send no Origin header — unaffected."""
    status, _ = _get(tcp_daemon)
    assert status == 200


def test_tcp_rebound_host_with_matching_origin_rejected(tcp_daemon):
    """Even a self-consistent rebind (Host == Origin host) hits the allowlist."""
    status, _ = _get(tcp_daemon, host="evil.example", origin="http://evil.example")
    assert status == 403


def test_tcp_query_param_token_deprecated(tmp_path):
    """?token= is gone; only the Authorization header authenticates."""
    from ned import settings
    from ned.daemon import NedDaemon
    old_token = settings.web_token
    settings.web_token = "secret-test-token"
    daemon = NedDaemon(
        socket_path=str(tmp_path / "deprecated.sock"),
        enable_tcp=True,
        tcp_host="127.0.0.1",
        tcp_port=0,
    )
    daemon.start()
    try:
        assert daemon._tcp_server is not None
        port = daemon._tcp_server.server_port
        status, _ = _get(port, path="/api/v1/tags?token=secret-test-token")
        assert status == 401
        status, _ = _get(port, token="secret-test-token", path="/api/v1/tags")
        assert status == 200
    finally:
        daemon.stop()
        settings.web_token = old_token


# -- host-header parsing / allowlist construction ------------------------------


def test_parse_host_header_variants():
    parse = NedRequestHandler._parse_host_header
    assert parse(SimpleNamespace(headers={"Host": "127.0.0.1:8080"})) == ("127.0.0.1", 8080)
    assert parse(SimpleNamespace(headers={"Host": "localhost"})) == ("localhost", None)
    assert parse(SimpleNamespace(headers={"Host": "[::1]:8080"})) == ("::1", 8080)
    assert parse(SimpleNamespace(headers={"Host": "100.100.1.2:8080"})) == ("100.100.1.2", 8080)
    assert parse(SimpleNamespace(headers={"Host": "ned.tailnet.ts.net"})) == ("ned.tailnet.ts.net", None)
    assert parse(SimpleNamespace(headers={"Host": ""})) == ("", None)


def test_compute_allowed_hostnames(monkeypatch):
    from ned import daemon
    monkeypatch.setattr(daemon, "detect_tailscale", lambda: {
        "serve_url": "https://ned.tailnet.ts.net",
        "serve_proxy": "http://127.0.0.1:8080",
        "ts_ip": "100.100.1.2",
        "ts_dns": "ned.tailnet.ts.net.",
    })
    names = daemon._compute_allowed_hostnames("127.0.0.1")
    assert {"localhost", "127.0.0.1", "100.100.1.2", "ned.tailnet.ts.net"} <= names
    # 0.0.0.0 wildcard binds contribute nothing to the allowlist
    assert "0.0.0.0" not in daemon._compute_allowed_hostnames("0.0.0.0")