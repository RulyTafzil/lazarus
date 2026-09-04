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

Provides access to the shared NedClient instance and optional daemon lifecycle
management for the desktop application.
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

# is_ned_active() is called on hot paths (every panel refresh, every
# keypress action, autocomplete, command bar). Each call used to open a
# fresh socket + HTTP ping, so resolve once and cache briefly. The cache
# is keyed on the NED-related env vars so tests (which flip
# LAZARUS_DISABLE_NED / NED_SOCK between cases) never see a stale value.
_NED_ACTIVE_TTL = 2.0
_ned_active_cache: Optional[tuple[float, bool]] = None
_ned_active_env: Optional[tuple[str, str, str, str]] = None


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
    """Reset cached client singleton and the active-state cache."""
    global _CLIENT_INSTANCE, _ned_active_cache, _ned_active_env
    if _CLIENT_INSTANCE is not None:
        _CLIENT_INSTANCE.close()
        _CLIENT_INSTANCE = None
    _ned_active_cache = None
    _ned_active_env = None


def _env_signature() -> tuple[str, str, str, str]:
    return (
        os.environ.get("LAZARUS_DISABLE_NED", ""),
        os.environ.get("NED_SOCK", ""),
        os.environ.get("NED_URL", ""),
        os.environ.get("NED_TOKEN", ""),
    )


def is_ned_active(force: bool = False) -> bool:
    """Check if the NED daemon is reachable and responding.

    Results are cached for a short TTL; pass ``force=True`` for call
    sites that must see a fresh answer (e.g. daemon-spawn polling loops).
    """
    global _ned_active_cache, _ned_active_env
    sig = _env_signature()
    if not force and _ned_active_cache is not None and _ned_active_env == sig:
        ts, val = _ned_active_cache
        if time.monotonic() - ts < _NED_ACTIVE_TTL:
            return val

    if os.environ.get("LAZARUS_DISABLE_NED") == "1":
        val = False
    else:
        try:
            val = get_client().ping()
        except Exception:
            val = False

    _ned_active_cache = (time.monotonic(), val)
    _ned_active_env = sig
    return val


def ensure_daemon(timeout: float = 3.0) -> bool:
    """Ensure the NED daemon is running, spawning it if necessary."""
    if is_ned_active(force=True):
        return True

    if os.environ.get("LAZARUS_DISABLE_NED") == "1":
        return False

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
        if is_ned_active(force=True):
            logger.info("NED daemon connected successfully")
            return True
        time.sleep(0.1)

    logger.warning("Timed out waiting for NED daemon to respond on %s", socket_path)
    return False