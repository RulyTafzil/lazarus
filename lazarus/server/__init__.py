"""Lazarus mobile web server and REST API package."""
from __future__ import annotations

from .app import create_server, run_server

__all__ = ["create_server", "run_server"]
