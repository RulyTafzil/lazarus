"""Tests for Tailscale detection, ned --status, and ned-client ping/status reporting."""

import json
import subprocess
from unittest.mock import MagicMock

import pytest

from ned.tailscale import detect_tailscale, get_recommended_client_url
from ned.main import _show_status
from ned import client


def test_detect_tailscale_serve_active(monkeypatch):
    def mock_run(cmd, *args, **kwargs):
        if cmd[:3] == ["tailscale", "serve", "status"]:
            data = {
                "Web": {
                    "node.tailnet.ts.net:443": {
                        "Handlers": {"/": {"Proxy": "http://127.0.0.1:8080"}}
                    }
                }
            }
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(data))
        if cmd[:2] == ["tailscale", "status"]:
            data = {"Self": {"TailscaleIPs": ["100.64.1.2"], "DNSName": "node.tailnet.ts.net."}}
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(data))
        return subprocess.CompletedProcess(cmd, 1, stdout="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    info = detect_tailscale()
    assert info["serve_url"] == "https://node.tailnet.ts.net"
    assert info["serve_proxy"] == "http://127.0.0.1:8080"
    assert info["ts_ip"] == "100.64.1.2"
    assert info["ts_dns"] == "node.tailnet.ts.net"

    url = get_recommended_client_url("127.0.0.1", 8080, info)
    assert url == "https://node.tailnet.ts.net"


def test_detect_tailscale_direct_ip_when_no_serve(monkeypatch):
    def mock_run(cmd, *args, **kwargs):
        if cmd[:3] == ["tailscale", "serve", "status"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="")
        if cmd[:2] == ["tailscale", "status"]:
            data = {"Self": {"TailscaleIPs": ["100.64.1.5"], "DNSName": "server.tailnet.ts.net."}}
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(data))
        return subprocess.CompletedProcess(cmd, 1, stdout="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    info = detect_tailscale()
    assert info["serve_url"] is None
    assert info["ts_ip"] == "100.64.1.5"
    assert info["ts_dns"] == "server.tailnet.ts.net"

    url = get_recommended_client_url("127.0.0.1", 8080, info)
    assert url == "http://100.64.1.5:8080"


def test_detect_tailscale_absent(monkeypatch):
    def mock_run(cmd, *args, **kwargs):
        raise FileNotFoundError("tailscale command not found")

    monkeypatch.setattr(subprocess, "run", mock_run)

    info = detect_tailscale()
    assert info["serve_url"] is None
    assert info["ts_ip"] is None
    assert info["ts_dns"] is None

    url = get_recommended_client_url("127.0.0.1", 8080, info)
    assert url == "http://127.0.0.1:8080"


def test_ned_status_stopped_output(monkeypatch, capsys):
    from ned.main import _show_status
    import argparse

    args = argparse.Namespace(socket="/tmp/nonexistent_socket.sock")
    monkeypatch.setattr("ned.main.detect_tailscale", lambda: {
        "serve_url": "https://test.ts.net",
        "serve_proxy": "http://127.0.0.1:8080",
        "ts_ip": "100.1.2.3",
        "ts_dns": "test.ts.net",
    })

    ret = _show_status(args)
    assert ret == 1

    out = capsys.readouterr().out
    assert "NED Daemon: STOPPED" in out
    assert "Unix socket: /tmp/nonexistent_socket.sock, status: inactive" in out
    assert "Tailscale Serve: https://test.ts.net" in out
    assert 'export NED_URL="https://test.ts.net"' in out


def test_ned_client_ping_and_status(capsys, monkeypatch):
    class FakeClient:
        def __init__(self, socket_path=None, base_url=None, token=None):
            self.socket_path = socket_path or "/tmp/test.sock"
            self.base_url = base_url
            self.token = token

        def ping(self):
            return True

        def health(self):
            return {"service": "ned", "version": "1.0"}

    monkeypatch.setattr(client, "NedClient", FakeClient)

    # Test ping
    ret = client.main(["ping"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "NED is reachable and responding at Unix socket" in out

    # Test status
    ret = client.main(["status"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "NED is RUNNING and responding" in out
    assert "version: 1.0" in out
