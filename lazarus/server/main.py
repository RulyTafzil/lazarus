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
"""CLI entry point for the Lazarus mobile web server.

Usage:
    lazarus-web [--host 0.0.0.0] [--port 8080] [--token SECRET]
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys

from .. import config
from .. import settings
from .app import run_server


def _find_tailscale_ip() -> str | None:
    """Detect Tailscale 100.x.y.z IP address if available."""
    try:
        # Check hostname IPs or network interfaces
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if ip.startswith('100.'):
                return ip
    except Exception:
        pass
    return None


def main() -> None:
    """Parse arguments, load config, and launch the server."""
    parser = argparse.ArgumentParser(description="Lazarus Mobile Web Server")
    parser.add_argument(
        '--host',
        default=None,
        help="Host to bind (defaults to settings.web_host or 127.0.0.1)",
    )
    parser.add_argument(
        '--port',
        type=int,
        default=None,
        help="Port to bind (defaults to settings.web_port or 8080)",
    )
    parser.add_argument(
        '--token',
        default=None,
        help="Optional bearer token for authentication",
    )
    parser.add_argument(
        '--log-level',
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help="Log level",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Load config without requiring a full Qt application
    try:
        config.load_config()
    except Exception as e:
        print(f"Warning: could not load config.py: {e}", file=sys.stderr)

    if args.token is not None:
        settings.web_token = args.token

    target_host = args.host if args.host is not None else getattr(settings, 'web_host', '127.0.0.1')
    target_port = args.port if args.port is not None else getattr(settings, 'web_port', 8080)

    ts_ip = _find_tailscale_ip()
    if ts_ip and target_host in ('0.0.0.0', ts_ip):
        print(f"Tailscale mobile URL: http://{ts_ip}:{target_port}")

    run_server(host=target_host, port=target_port)


if __name__ == '__main__':
    main()
