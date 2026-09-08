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
"""CLI entry point for the Notmuch Email Daemon (NED)."""

from __future__ import annotations

import argparse
import http.client
import logging
import os
import signal
import socket
import subprocess
import sys
import time

from . import config, settings
from .daemon import NedDaemon, get_default_socket_path, insecure_tcp_error
from .tailscale import detect_tailscale, get_recommended_client_url

logger = logging.getLogger("ned")


def _detect_tailscale_ip() -> str | None:
    """Detect Tailscale IPv4 address if available."""
    info = detect_tailscale()
    return info.get("ts_ip")


def _show_status(args: argparse.Namespace) -> int:
    """Check if the NED daemon is running and display connection URLs."""
    sock_path = args.socket or get_default_socket_path()
    is_socket_alive = False
    if os.path.exists(sock_path):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect(sock_path)
            s.close()
            is_socket_alive = True
        except OSError:
            pass

    try:
        config.load_config()
    except Exception:
        pass

    web_host = getattr(settings, "web_host", "127.0.0.1") or "127.0.0.1"
    web_port = getattr(settings, "web_port", 8080) or 8080

    is_tcp_alive = False
    try:
        conn = http.client.HTTPConnection(web_host, web_port, timeout=1.0)
        conn.request("GET", "/api/v1/ping")
        res = conn.getresponse()
        if res.status == 200:
            is_tcp_alive = True
        conn.close()
    except Exception:
        pass

    ts_info = detect_tailscale()
    is_running = is_socket_alive or is_tcp_alive

    status_str = "RUNNING" if is_running else "STOPPED"
    print(f"NED Daemon: {status_str}")

    sock_state = "active" if is_socket_alive else "inactive"
    print(f"Unix socket: {sock_path}, status: {sock_state}")

    tcp_state = "responding" if is_tcp_alive else "not responding"
    print(f"Local bind: http://{web_host}:{web_port}, status: {tcp_state}")

    if ts_info["serve_url"]:
        print(f"Tailscale Serve: {ts_info['serve_url']}")
    if ts_info["ts_ip"]:
        dns_str = f", MagicDNS: {ts_info['ts_dns']}" if ts_info["ts_dns"] else ""
        print(f"Tailscale IP: {ts_info['ts_ip']}{dns_str}")

    client_url = get_recommended_client_url(web_host, web_port, ts_info)
    print("\nTo connect remote Lazarus desktop clients, run on the client:")
    print(f'  export NED_URL="{client_url}"')

    return 0 if is_running else 1


def main() -> int:
    """Run the NED daemon process."""
    parser = argparse.ArgumentParser(prog="ned", description="Notmuch Email Daemon (NED)")
    parser.add_argument(
        "--socket",
        help=f"Unix domain socket path (default: {get_default_socket_path()})",
    )
    parser.add_argument(
        "--host",
        help="Host/IP to bind TCP listener (defaults to Tailscale IP, settings.web_host, or 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Port to bind TCP listener (defaults to settings.web_port or 8080)",
    )
    parser.add_argument(
        "--no-tcp",
        action="store_true",
        help="Disable TCP listener completely (listen only on Unix domain socket)",
    )
    parser.add_argument(
        "--token",
        help="DEPRECATED: bearer token for non-browser HTTP clients (Authorization header only; the PWA no longer supports tokens — use Tailscale ACLs or the Unix socket)",
    )
    parser.add_argument(
        "--sync-interval",
        type=int,
        default=None,
        help="Periodic background sync interval in seconds (default: settings.sync_mail_interval; -1 disables)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Generate ~/.config/ned/config.py from Notmuch and local maildir inspection and exit",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Check whether the NED daemon is running and show connection URLs",
    )
    parser.add_argument(
        "--allow-insecure",
        action="store_true",
        help="Override the refusal to expose an unauthenticated TCP listener on a non-loopback/non-Tailscale host",
    )

    args = parser.parse_args()

    if args.status:
        return _show_status(args)

    if args.init_config:
        try:
            r_path = config.rules_path()
            rules_existed = os.path.isfile(r_path)
            cfg_path, backup_path = config.init_config()
            if backup_path:
                print(f"Backed up existing config to: {backup_path}")
            print(f"Wrote NED config: {cfg_path}")
            if rules_existed:
                print(f"Preserved existing filter rules: {r_path}")
            else:
                print(f"Created sample filter rules: {r_path}")
            ts_info = detect_tailscale()
            if ts_info["serve_url"]:
                print(f"Tailscale Serve active: {ts_info['serve_url']}")
                print(f'Remote client connection: export NED_URL="{ts_info["serve_url"]}"')
            elif ts_info["ts_ip"]:
                dns_str = f", MagicDNS: {ts_info['ts_dns']}" if ts_info["ts_dns"] else ""
                print(f"Detected Tailscale IP: {ts_info['ts_ip']}{dns_str}")
                print(f'Remote client connection: export NED_URL="http://{ts_info["ts_ip"]}:8080"')
            return 0
        except Exception as e:
            print(f"Failed to init config: {e}", file=sys.stderr)
            return 1

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Load configuration: NED is standalone and reads only ~/.config/ned/config.py.
    # (The daemon does not follow the desktop's ~/.config/lazarus/config.py.)
    try:
        cfg_path = config.load_config()
        logger.info("Loaded configuration from %s", cfg_path)
    except Exception as e:
        logger.warning("Could not load configuration: %s", e)

    # Resolve token override
    if args.token:
        settings.web_token = args.token

    # Resolve host: prioritize CLI -> Tailscale auto-detect -> settings -> localhost
    host = args.host
    if not host and not args.no_tcp:
        ts_info = detect_tailscale()
        if ts_info["serve_url"]:
            host = getattr(settings, "web_host", "127.0.0.1") or "127.0.0.1"
        elif ts_info["ts_ip"]:
            configured_host = getattr(settings, "web_host", None)
            host = configured_host if configured_host and configured_host != "127.0.0.1" else ts_info["ts_ip"]
            logger.info("Detected Tailscale IP: %s", ts_info["ts_ip"])
        else:
            host = getattr(settings, "web_host", "127.0.0.1")

    port = args.port or getattr(settings, "web_port", 8080)

    enable_tcp = not args.no_tcp
    if enable_tcp:
        # Refuse-guard: never bind an unauthenticated TCP listener outside
        # loopback/Tailscale unless explicitly overridden.
        bind_err = insecure_tcp_error(
            host or "127.0.0.1", getattr(settings, "web_token", "") or "", args.allow_insecure)
        if bind_err:
            logger.error(bind_err)
            print(bind_err, file=sys.stderr)
            sys.exit(2)

    # Background sync scheduler: CLI flag wins, otherwise follow the NED
    # config (settings.sync_mail_interval; -1 disables). The desktop no
    # longer runs its own sync timer — the daemon owns periodic sync.
    sync_interval = args.sync_interval
    if sync_interval is None:
        cfg_interval = getattr(settings, "sync_mail_interval", 300)
        sync_interval = cfg_interval if cfg_interval and cfg_interval > 0 else None

    daemon = NedDaemon(
        socket_path=args.socket,
        tcp_host=host,
        tcp_port=port,
        enable_tcp=enable_tcp,
        sync_interval_seconds=sync_interval,
    )

    def _shutdown_handler(sig: int, frame: object) -> None:
        logger.info("Received signal %s, stopping NED...", signal.strsignal(sig))
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    try:
        daemon.start()
        print("NED (Notmuch Email Daemon) running.")
        print(f"  Unix socket: {daemon.socket_path}")
        if not args.no_tcp:
            print(f"  TCP HTTP:    http://{host}:{port}")
        # Keep main thread alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user, shutting down...")
    except Exception as e:
        logger.error("Daemon error: %s", e)
        return 1
    finally:
        daemon.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
