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
"""Server-Sent Events (SSE) invalidation broadcaster for NED.

Pushes minimal invalidation events to connected clients so they can refresh
their local views without polling.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any, Optional, Set

logger = logging.getLogger(__name__)


class EventBroadcaster:
    """Manages active SSE client subscriber queues and broadcasts events."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: Set[queue.Queue[bytes]] = set()

    def subscribe(self) -> queue.Queue[bytes]:
        """Register a new client queue to receive SSE events."""
        q: queue.Queue[bytes] = queue.Queue(maxsize=128)
        with self._lock:
            self._subscribers.add(q)
        logger.debug("Client subscribed to SSE stream (total: %d)", len(self._subscribers))
        return q

    def unsubscribe(self, q: queue.Queue[bytes]) -> None:
        """Remove a disconnected client queue."""
        with self._lock:
            self._subscribers.discard(q)
        logger.debug("Client unsubscribed from SSE stream (total: %d)", len(self._subscribers))

    def broadcast(self, event_name: str, payload: dict[str, Any]) -> None:
        """Broadcast an event to all connected subscribers."""
        msg = f"event: {event_name}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")
        with self._lock:
            dead: Set[queue.Queue[bytes]] = set()
            for q in self._subscribers:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead.add(q)
            for q in dead:
                self._subscribers.discard(q)

    def broadcast_invalidate(
        self, scope: str, item_id: Optional[str] = None, reason: Optional[str] = None
    ) -> None:
        """Emit a cache invalidation event for threads or a specific thread."""
        payload: dict[str, Any] = {"scope": scope}
        if item_id:
            payload["id"] = item_id
        if reason:
            payload["reason"] = reason
        self.broadcast("invalidate", payload)


# Process-wide event broadcaster instance
broadcaster = EventBroadcaster()
