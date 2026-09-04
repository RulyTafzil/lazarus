"""TCP bind security guard — unauthenticated non-loopback/Tailscale refusal."""
import os
import subprocess
import sys

import pytest

from ned.daemon import insecure_tcp_error, is_loopback_host, is_tailscale_host


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