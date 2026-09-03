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
"""Daemon lifecycle manager for NED.

Manages dual listeners (Unix domain stream socket and TCP server),
background synchronization scheduler, and graceful shutdown.
"""

from __future__ import annotations

import http.server
import logging
import os
from pathlib import Path
import signal
import socket
import socketserver
import threading
import time
from typing import Optional

from .. import settings
from ..server import service
from .concurrency import mutation_lock
from .events import broadcaster
from .handler import NedRequestHandler

logger = logging.getLogger(__name__)


def get_default_socket_path() -> str:
    """Return canonical path for the NED Unix domain socket."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        base = Path(runtime_dir) / "ned"
    else:
        base = Path(os.path.expanduser("~/.local/share/lazarus/ned"))
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "ned.sock")


class ThreadingUnixStreamServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Threaded HTTP server over Unix domain socket."""

    daemon_threads = True
    allow_reuse_address = True


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Threaded HTTP server over TCP."""

    daemon_threads = True
    allow_reuse_address = True


class NedDaemon:
    """The Notmuch Email Daemon (NED) server instance."""

    def __init__(
        self,
        socket_path: Optional[str] = None,
        tcp_host: Optional[str] = None,
        tcp_port: Optional[int] = None,
        enable_tcp: bool = True,
        sync_interval_seconds: Optional[int] = None,
    ) -> None:
        self.socket_path = socket_path or get_default_socket_path()
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.enable_tcp = enable_tcp
        self.sync_interval_seconds = sync_interval_seconds

        self._unix_server: Optional[ThreadingUnixStreamServer] = None
        self._tcp_server: Optional[ThreadingHTTPServer] = None
        self._unix_thread: Optional[threading.Thread] = None
        self._tcp_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._running = False
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Initialize sockets and start listener threads."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()

        # 1. Start Unix domain socket server
        self._start_unix_listener()

        # 2. Start TCP server if enabled
        if self.enable_tcp:
            self._start_tcp_listener()

        # 3. Start background sync scheduler if configured
        if self.sync_interval_seconds and self.sync_interval_seconds > 0:
            self._start_sync_scheduler()

    def _start_unix_listener(self) -> None:
        """Bind and start the Unix domain socket server."""
        sock_file = Path(self.socket_path)
        sock_file.parent.mkdir(parents=True, exist_ok=True)

        if sock_file.exists():
            # Check if an active instance is running
            test_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                test_sock.connect(self.socket_path)
                test_sock.close()
                raise RuntimeError(f"Another NED instance is already running on {self.socket_path}")
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                sock_file.unlink(missing_ok=True)

        self._unix_server = ThreadingUnixStreamServer(self.socket_path, NedRequestHandler)
        # Secure socket permissions to user-only
        try:
            os.chmod(self.socket_path, 0o600)
        except OSError as e:
            logger.warning("Could not set 0600 permissions on %s: %s", self.socket_path, e)

        self._unix_thread = threading.Thread(
            target=self._unix_server.serve_forever,
            name="NED-UnixListener",
            daemon=True,
        )
        self._unix_thread.start()
        logger.info("NED listening on Unix socket: %s", self.socket_path)

    def _start_tcp_listener(self) -> None:
        """Bind and start the TCP HTTP server."""
        host = str(self.tcp_host) if self.tcp_host else str(getattr(settings, "web_host", "127.0.0.1"))
        port = int(self.tcp_port if self.tcp_port is not None else getattr(settings, "web_port", 8080))

        try:
            self._tcp_server = ThreadingHTTPServer((host, port), NedRequestHandler)
            self._tcp_thread = threading.Thread(
                target=self._tcp_server.serve_forever,
                name="NED-TCPListener",
                daemon=True,
            )
            self._tcp_thread.start()
            logger.info("NED listening on http://%s:%d", host, port)
        except OSError as e:
            logger.error("Failed to bind TCP server on %s:%d: %s", host, port, e)

    def _start_sync_scheduler(self) -> None:
        """Start periodic background sync thread."""
        def _sync_loop() -> None:
            interval = self.sync_interval_seconds or 300
            logger.info("Starting background sync loop (every %ds)", interval)
            while not self._stop_event.wait(interval):
                logger.debug("Background sync triggered by NED scheduler")
                with mutation_lock:
                    try:
                        ok, msg = service.sync_mail()
                        if ok:
                            broadcaster.broadcast_invalidate("threads", reason="sync")
                    except Exception as e:
                        logger.warning("Scheduled sync failed: %s", e)

        self._sync_thread = threading.Thread(target=_sync_loop, name="NED-SyncScheduler", daemon=True)
        self._sync_thread.start()

    def stop(self) -> None:
        """Shutdown listeners and clean up filesystem artifacts."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()

        # Stop servers
        if self._unix_server:
            self._unix_server.shutdown()
            self._unix_server.server_close()
            self._unix_server = None

        if self._tcp_server:
            self._tcp_server.shutdown()
            self._tcp_server.server_close()
            self._tcp_server = None

        # Clean up Unix socket file
        try:
            Path(self.socket_path).unlink(missing_ok=True)
        except OSError:
            pass

        logger.info("NED daemon stopped cleanly")
