"""Notmuch Email Daemon (NED) package."""

from .concurrency import MutationLock, mutation_lock
from .daemon import NedDaemon, get_default_socket_path
from .events import EventBroadcaster, broadcaster

__all__ = [
    "NedDaemon",
    "MutationLock",
    "mutation_lock",
    "EventBroadcaster",
    "broadcaster",
    "get_default_socket_path",
]
