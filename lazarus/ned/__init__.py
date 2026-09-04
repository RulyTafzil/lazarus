"""Notmuch Email Daemon (NED) package."""

from .client import (
    NedAuthenticationError,
    NedClient,
    NedConnectionError,
    NedError,
    NedEvent,
    NedNotFoundError,
    NedResponseError,
)
from .concurrency import MutationLock, mutation_lock
from .daemon import NedDaemon, get_default_socket_path
from .events import EventBroadcaster, broadcaster

__all__ = [
    "NedDaemon",
    "NedClient",
    "NedEvent",
    "NedError",
    "NedConnectionError",
    "NedResponseError",
    "NedAuthenticationError",
    "NedNotFoundError",
    "MutationLock",
    "mutation_lock",
    "EventBroadcaster",
    "broadcaster",
    "get_default_socket_path",
]

