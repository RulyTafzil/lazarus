"""Tailscale environment inspection and connection URL detection for NED."""

from __future__ import annotations

import json
import subprocess
from typing import Any, Optional, TypedDict


class TailscaleInfo(TypedDict):
    serve_url: Optional[str]
    serve_proxy: Optional[str]
    ts_ip: Optional[str]
    ts_dns: Optional[str]


def detect_tailscale() -> TailscaleInfo:
    """Detect Tailscale Serve status, IPv4 address, and MagicDNS name."""
    serve_url: Optional[str] = None
    serve_proxy: Optional[str] = None

    try:
        r = subprocess.run(
            ["tailscale", "serve", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            web = data.get("Web", {})
            for host_port, details in web.items():
                handlers = details.get("Handlers", {})
                root_h = handlers.get("/", {})
                proxy = root_h.get("Proxy", "")
                if proxy:
                    host = host_port.split(":")[0]
                    serve_url = f"https://{host}"
                    serve_proxy = proxy
                    break
    except Exception:
        pass

    ts_ip: Optional[str] = None
    ts_dns: Optional[str] = None

    try:
        r = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            self_node = data.get("Self", {})
            ips = self_node.get("TailscaleIPs", [])
            ts_ip = next((ip for ip in ips if "." in ip), None)
            dns = self_node.get("DNSName", "").rstrip(".")
            ts_dns = dns if dns else None
    except Exception:
        pass

    if not ts_ip:
        try:
            r = subprocess.run(
                ["tailscale", "ip", "-4"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if r.returncode == 0 and r.stdout.strip():
                ts_ip = r.stdout.strip()
        except Exception:
            pass

    return {
        "serve_url": serve_url,
        "serve_proxy": serve_proxy,
        "ts_ip": ts_ip,
        "ts_dns": ts_dns,
    }


def get_recommended_client_url(
    web_host: str, web_port: int, ts_info: Optional[TailscaleInfo] = None
) -> str:
    """Return the best client connection URL based on detected environment."""
    info = ts_info if ts_info is not None else detect_tailscale()
    if info["serve_url"]:
        return info["serve_url"]
    if info["ts_ip"]:
        return f"http://{info['ts_ip']}:{web_port}"
    if web_host and web_host not in ("0.0.0.0", "127.0.0.1", "localhost"):
        return f"http://{web_host}:{web_port}"
    return f"http://127.0.0.1:{web_port}"
