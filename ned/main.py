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
import logging
import signal
import subprocess
import sys
import time

from . import config, settings
from .daemon import NedDaemon, get_default_socket_path

logger = logging.getLogger("ned")


def _detect_tailscale_ip() -> str | None:
    """Detect Tailscale IPv4 address if available."""
    try:
        res = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            ip = res.stdout.strip()
            if ip:
                return ip
    except Exception:
        pass
    return None


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
        help="Bearer token for remote HTTP authentication",
    )
    parser.add_argument(
        "--sync-interval",
        type=int,
        default=0,
        help="Periodic background sync interval in seconds (default: 0 = disabled)",
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
        help="Generate ~/.config/ned/config.py from ~/.config/lazarus/config.py and exit",
    )

    args = parser.parse_args()

    if args.init_config:
        try:
            cfg_path = config.init_config()
            print(f"Wrote NED config: {cfg_path} — review and edit it.")
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
        ts_ip = _detect_tailscale_ip()
        if ts_ip:
            host = ts_ip
            logger.info("Detected Tailscale IP: %s", ts_ip)
        else:
            host = getattr(settings, "web_host", "127.0.0.1")

    port = args.port or getattr(settings, "web_port", 8080)

    daemon = NedDaemon(
        socket_path=args.socket,
        tcp_host=host,
        tcp_port=port,
        enable_tcp=not args.no_tcp,
        sync_interval_seconds=args.sync_interval,
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
