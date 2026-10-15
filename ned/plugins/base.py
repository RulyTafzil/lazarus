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
"""Base protocol and execution context for NED post-sync plugins.

Post-sync plugins execute asynchronously after mail synchronization and static
rule application have completed. They receive an execution context containing
mail stats, a client handle for querying/modifying tags, and an invalidation
callback that broadcasts live Server-Sent Events (SSE) to connected clients.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class PostSyncContext:
    """Execution context passed to `on_post_sync` for each plugin.

    Attributes:
        stats: Dictionary of sync outcome counters (e.g. `{'new': 2, 'flagged': 0, 'deleted': 0}`).
        client: Client handle (e.g. `NedClient` or service accessor) to search and tag messages.
        logger: Logger instance scoped to the executing plugin.
        invalidate_fn: Callable to broadcast SSE cache invalidations to connected clients.
    """

    stats: dict[str, int]
    client: Any
    logger: logging.Logger
    invalidate_fn: Callable[[str, Optional[str], Optional[str]], None]

    def invalidate(
        self,
        scope: str = "threads",
        item_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Emit an SSE invalidation event to notify desktop clients (e.g. Lazarus GUI) to refresh."""
        if self.invalidate_fn is not None:
            try:
                self.invalidate_fn(scope, item_id, reason)
            except Exception as e:
                self.logger.warning("Failed to emit SSE invalidation from plugin: %s", e)


@runtime_checkable
class PostSyncPlugin(Protocol):
    """Protocol that any NED post-sync plugin implements."""

    name: str

    def on_post_sync(self, context: PostSyncContext) -> None:
        """Execute post-sync processing asynchronously.

        Args:
            context: The `PostSyncContext` instance containing stats, client, and invalidation hooks.
        """
        ...
