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
"""NED client singleton and daemon manager for Lazarus desktop.

Lazarus is a pure client of the Notmuch Email Daemon: every query and
mutation goes through ``NedClient``. There is deliberately no local
fallback — the daemon owns the notmuch index and all Maildir writes.
The desktop spawns NED on startup if it is not already running.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Optional

from .ned.client import NedClient, resolve_default_socket_path

logger = logging.getLogger(__name__)

_CLIENT_INSTANCE: Optional[NedClient] = None


def get_client() -> NedClient:
    """Return the shared NedClient instance."""
    global _CLIENT_INSTANCE
    if _CLIENT_INSTANCE is None:
        socket_path = os.environ.get("NED_SOCK")
        url = os.environ.get("NED_URL")
        token = os.environ.get("NED_TOKEN")
        if url:
            _CLIENT_INSTANCE = NedClient.http(base_url=url, token=token)
        else:
            path = socket_path or resolve_default_socket_path()
            _CLIENT_INSTANCE = NedClient.unix(socket_path=path)
    return _CLIENT_INSTANCE


def reset_client() -> None:
    """Reset the cached client singleton (used by tests between cases)."""
    global _CLIENT_INSTANCE
    if _CLIENT_INSTANCE is not None:
        _CLIENT_INSTANCE.close()
        _CLIENT_INSTANCE = None


def ensure_daemon(timeout: float = 5.0) -> bool:
    """Ensure the NED daemon is running, spawning it if necessary.

    Callers (``lazarus.app`` bootstrap) are expected to treat a ``False``
    result as fatal — Lazarus has no standalone mode. ``LAZARUS_DISABLE_NED=1``
    suppresses spawning for headless/CI diagnostics only; it does not
    re-enable any local fallback.
    """
    if os.environ.get("LAZARUS_DISABLE_NED") == "1":
        return False

    if get_client().ping():
        return True

    socket_path = os.environ.get("NED_SOCK") or resolve_default_socket_path()
    sock_p = Path(socket_path)
    sock_p.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "lazarus.ned.main",
        f"--socket={socket_path}",
    ]
    logger.info("Spawning NED daemon: %s", " ".join(cmd))
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception as exc:
        logger.warning("Failed spawning NED daemon: %s", exc)
        return False

    start = time.time()
    reset_client()
    while time.time() - start < timeout:
        if get_client().ping():
            logger.info("NED daemon connected successfully")
            return True
        time.sleep(0.1)

    logger.warning("Timed out waiting for NED daemon to respond on %s", socket_path)
    return False