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
"""Asynchronous runner for NED post-sync plugins.

Dequeues post-sync notifications and executes registered plugins inside a
dedicated background worker thread, ensuring mail sync is never delayed.
"""

from __future__ import annotations

import importlib
import logging
import os
import queue
import sys
import threading
from typing import Any, Callable, List, Optional, Union

from .base import PostSyncContext, PostSyncPlugin

logger = logging.getLogger(__name__)


def resolve_plugin(plugin_entry: Any) -> Optional[Any]:
    """Resolve a plugin entry (instance, class, callable, or import path string)."""
    if isinstance(plugin_entry, str):
        plugin_entry = plugin_entry.strip()
        if not plugin_entry:
            return None

        # Ensure user plugins directory is accessible on sys.path
        user_plugins_dir = os.path.expanduser("~/.config/ned/plugins")
        if os.path.isdir(user_plugins_dir) and user_plugins_dir not in sys.path:
            sys.path.insert(0, user_plugins_dir)
        config_dir = os.path.expanduser("~/.config/ned")
        if os.path.isdir(config_dir) and config_dir not in sys.path:
            sys.path.insert(0, config_dir)
        # Support module path or module.ClassName
        try:
            if ":" in plugin_entry:
                mod_name, obj_name = plugin_entry.split(":", 1)
                mod = importlib.import_module(mod_name)
                target = getattr(mod, obj_name)
            elif "." in plugin_entry:
                try:
                    # Try importing full path as module first
                    mod = importlib.import_module(plugin_entry)
                    # Look for default plugin attribute or class
                    for attr in ("Plugin", "default_plugin"):
                        if hasattr(mod, attr):
                            target = getattr(mod, attr)
                            break
                    else:
                        # Or split last segment
                        mod_name, obj_name = plugin_entry.rsplit(".", 1)
                        mod = importlib.import_module(mod_name)
                        target = getattr(mod, obj_name)
                except (ImportError, AttributeError):
                    mod_name, obj_name = plugin_entry.rsplit(".", 1)
                    mod = importlib.import_module(mod_name)
                    target = getattr(mod, obj_name)
            else:
                mod = importlib.import_module(plugin_entry)
                target = getattr(mod, "Plugin", getattr(mod, "default_plugin", mod))

            if isinstance(target, type):
                return target()
            return target
        except Exception as e:
            logger.error("Failed to load post-sync plugin %r: %s", plugin_entry, e)
            return None

    if isinstance(plugin_entry, type):
        try:
            return plugin_entry()
        except Exception as e:
            logger.error("Failed to instantiate plugin class %r: %s", plugin_entry, e)
            return None

    return plugin_entry


class PluginRunner:
    """Manages background asynchronous execution of post-sync plugins."""

    def __init__(
        self,
        plugins_provider: Optional[Callable[[], List[Any]]] = None,
        client_factory: Optional[Callable[[], Any]] = None,
        invalidate_fn: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
    ) -> None:
        self._plugins_provider = plugins_provider
        self._client_factory = client_factory
        self._invalidate_fn = invalidate_fn
        self._queue: queue.Queue[Optional[dict[str, int]]] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the background worker thread."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="NED-PluginRunner",
                daemon=True,
            )
            self._thread.start()
            logger.debug("Started NED post-sync plugin runner worker")

    def stop(self, timeout: float = 5.0) -> None:
        """Signal worker to stop and wait for completion."""
        with self._lock:
            if self._thread is None:
                return
            self._stop_event.set()
            self._queue.put(None)
            thread = self._thread
            self._thread = None

        if thread.is_alive():
            thread.join(timeout=timeout)
            logger.debug("Stopped NED post-sync plugin runner worker")

    def enqueue(self, stats: dict[str, int]) -> None:
        """Queue a post-sync event for background processing."""
        # Ensure worker is started if not already
        if self._thread is None or not self._thread.is_alive():
            self.start()
        self._queue.put(stats)

    def drain(self, timeout: Optional[float] = None) -> None:
        """Block until all currently enqueued plugin tasks have completed (useful for tests)."""
        self._queue.join()

    def _worker_loop(self) -> None:
        """Background loop executing queued tasks sequentially."""
        while not self._stop_event.is_set():
            try:
                task = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if task is None:
                self._queue.task_done()
                break

            try:
                self._execute_plugins(task)
            except Exception as e:
                logger.error("Unexpected error in plugin runner execution: %s", e, exc_info=True)
            finally:
                self._queue.task_done()

    def _execute_plugins(self, stats: dict[str, int]) -> None:
        """Execute each configured plugin within its PostSyncContext."""
        from .. import settings
        from ..events import broadcaster

        raw_plugins = self._plugins_provider() if self._plugins_provider else getattr(settings, "post_sync_plugins", [])
        if not raw_plugins:
            return

        # Prepare client handle
        client: Any = None
        if self._client_factory:
            try:
                client = self._client_factory()
            except Exception as e:
                logger.warning("Failed to create client for plugin context: %s", e)
        if client is None:
            from .. import service
            client = service

        inv_fn = self._invalidate_fn or broadcaster.broadcast_invalidate

        for raw in raw_plugins:
            plugin = resolve_plugin(raw)
            if plugin is None:
                continue

            plugin_name = getattr(plugin, "name", type(plugin).__name__)
            plugin_logger = logging.getLogger(f"ned.plugins.{plugin_name}")

            ctx = PostSyncContext(
                stats=stats,
                client=client,
                logger=plugin_logger,
                invalidate_fn=inv_fn,
            )

            try:
                if hasattr(plugin, "on_post_sync"):
                    plugin.on_post_sync(ctx)
                elif callable(plugin):
                    plugin(ctx)
                else:
                    logger.warning("Plugin %r does not implement on_post_sync or callable interface", plugin_name)
            except Exception as e:
                logger.error("Error executing post-sync plugin %s: %s", plugin_name, e, exc_info=True)


# Global singleton instance
default_runner = PluginRunner()
