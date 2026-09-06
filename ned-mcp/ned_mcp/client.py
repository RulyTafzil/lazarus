"""NED client adapter for ned-mcp.

Attempts to use ned.client if available. If run in a separate virtual environment
without ned installed, provides a standalone client talking over Unix socket or HTTP.
"""

from __future__ import annotations

import base64
from http.client import HTTPConnection, HTTPSConnection
import json
import os
from pathlib import Path
import socket
from typing import Any, Optional
import urllib.parse

try:
    from ned.client import (  # type: ignore
        NedAuthenticationError,
        NedClient,
        NedConnectionError,
        NedError,
        NedNotFoundError,
        NedResponseError,
        resolve_default_socket_path,
    )
except ImportError:
    # Standalone fallback implementation

    def resolve_default_socket_path() -> str:
        xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
        if xdg_runtime:
            return str(Path(xdg_runtime) / "ned" / "ned.sock")
        return f"/run/user/{os.getuid()}/ned/ned.sock"

    class NedError(Exception):
        """Base client error."""

    class NedConnectionError(NedError):
        """Connection error."""

    class NedResponseError(NedError):
        """Response error."""

        def __init__(self, status: int, message: str, data: Any = None) -> None:
            super().__init__(f"NED error {status}: {message}")
            self.status = status
            self.message = message
            self.data = data

    class NedNotFoundError(NedResponseError):
        """Not found error."""

    class NedAuthenticationError(NedResponseError):
        """Authentication error."""

    class _UnixHTTPConnection(HTTPConnection):
        def __init__(self, socket_path: str, timeout: float = 10.0) -> None:
            super().__init__("localhost", timeout=timeout)
            self.socket_path = socket_path

        def connect(self) -> None:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect(self.socket_path)
            self.sock = sock

    class NedClient:  # type: ignore[no-redef]
        """Standalone client for interacting with the NED daemon."""

        def __init__(
            self,
            socket_path: Optional[str] = None,
            base_url: Optional[str] = None,
            token: Optional[str] = None,
            timeout: float = 15.0,
        ) -> None:
            if socket_path is None and base_url is None:
                env_sock = os.environ.get("NED_SOCK")
                env_url = os.environ.get("NED_URL")
                if env_sock:
                    socket_path = env_sock
                elif env_url:
                    base_url = env_url
                else:
                    socket_path = resolve_default_socket_path()

            self.socket_path = socket_path
            self.base_url = base_url.rstrip("/") if base_url else None
            self.token = token or os.environ.get("NED_TOKEN")
            self.timeout = timeout

        def _get_connection(self) -> HTTPConnection:
            if self.socket_path:
                return _UnixHTTPConnection(self.socket_path, timeout=self.timeout)
            if not self.base_url:
                raise NedConnectionError("No socket path or base URL configured.")
            parsed = urllib.parse.urlparse(self.base_url)
            host = parsed.hostname or "localhost"
            port = parsed.port
            if parsed.scheme == "https":
                return HTTPSConnection(host, port=port or 443, timeout=self.timeout)
            return HTTPConnection(host, port=port or 80, timeout=self.timeout)

        def _request_json(
            self,
            method: str,
            path: str,
            query_params: Optional[dict[str, Any]] = None,
            json_body: Optional[dict[str, Any]] = None,
        ) -> Any:
            headers = {"Accept": "application/json"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            body = None
            if json_body is not None:
                body = json.dumps(json_body).encode("utf-8")
                headers["Content-Type"] = "application/json; charset=utf-8"

            full_path = path
            if query_params:
                qstr = urllib.parse.urlencode(
                    {k: v for k, v in query_params.items() if v is not None}
                )
                if qstr:
                    full_path = f"{full_path}?{qstr}"

            conn = self._get_connection()
            try:
                conn.request(method, full_path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
            except (OSError, socket.error) as err:
                raise NedConnectionError(f"Connection failed: {err}") from err
            finally:
                conn.close()

            if resp.status == 401:
                raise NedAuthenticationError(resp.status, "Authentication required", data)
            if resp.status == 404:
                raise NedNotFoundError(resp.status, "Resource not found", data)
            if resp.status >= 400:
                raise NedResponseError(resp.status, f"HTTP error {resp.status}", data)

            if not data:
                return None
            try:
                return json.loads(data.decode("utf-8"))
            except Exception as err:
                raise NedResponseError(resp.status, f"Invalid JSON response: {err}") from err

        def ping(self) -> bool:
            try:
                res = self._request_json("GET", "/api/v1/ping")
                return isinstance(res, dict) and res.get("status") == "pong"
            except Exception:
                return False

        def search(self, query: str, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
            data = self._request_json(
                "GET", "/api/v1/threads", query_params={"q": query, "limit": limit, "offset": offset}
            )
            return list(data) if isinstance(data, list) else []

        def get_thread(self, thread_id: str) -> dict[str, Any]:
            clean_id = urllib.parse.quote(thread_id.removeprefix("thread:"), safe="")
            data = self._request_json("GET", f"/api/v1/threads/{clean_id}", query_params={"full": 1})
            if not isinstance(data, dict):
                raise NedResponseError(200, "Unexpected thread format")
            return data

        def get_message(self, msg_id: str) -> dict[str, Any]:
            clean_id = urllib.parse.quote(msg_id, safe="")
            data = self._request_json("GET", f"/api/v1/messages/{clean_id}")
            if not isinstance(data, dict):
                raise NedResponseError(200, "Unexpected message format")
            return data

        def count(self, query: str, output: str = "threads") -> int:
            res = self._request_json(
                "GET", "/api/v1/count", query_params={"q": query, "output": output}
            )
            if isinstance(res, dict) and "count" in res:
                return int(res["count"])
            return 0

        def modify_tags(
            self,
            queries: Optional[str | list[str]] = None,
            threads: Optional[str | list[str]] = None,
            messages: Optional[str | list[str]] = None,
            add: Optional[str | list[str]] = None,
            remove: Optional[str | list[str]] = None,
        ) -> bool:
            payload: dict[str, Any] = {
                "queries": [queries] if isinstance(queries, str) else list(queries or []),
                "threads": [threads] if isinstance(threads, str) else list(threads or []),
                "messages": [messages] if isinstance(messages, str) else list(messages or []),
                "add_tags": [add] if isinstance(add, str) else list(add or []),
                "remove_tags": [remove] if isinstance(remove, str) else list(remove or []),
            }
            res = self._request_json("POST", "/api/v1/tags", json_body=payload)
            return bool(isinstance(res, dict) and (res.get("status") == "ok" or res.get("ok")))

        def trash_batch(
            self,
            queries: Optional[str | list[str]] = None,
            threads: Optional[str | list[str]] = None,
            messages: Optional[str | list[str]] = None,
        ) -> bool:
            payload: dict[str, Any] = {
                "queries": [queries] if isinstance(queries, str) else list(queries or []),
                "threads": [threads] if isinstance(threads, str) else list(threads or []),
                "messages": [messages] if isinstance(messages, str) else list(messages or []),
                "unmark": False,
            }
            res = self._request_json("POST", "/api/v1/trash", json_body=payload)
            return bool(isinstance(res, dict) and (res.get("status") == "ok" or res.get("ok")))

        def restore_batch(
            self,
            queries: Optional[str | list[str]] = None,
            threads: Optional[str | list[str]] = None,
            messages: Optional[str | list[str]] = None,
        ) -> bool:
            payload: dict[str, Any] = {
                "queries": [queries] if isinstance(queries, str) else list(queries or []),
                "threads": [threads] if isinstance(threads, str) else list(threads or []),
                "messages": [messages] if isinstance(messages, str) else list(messages or []),
                "unmark": False,
            }
            res = self._request_json("POST", "/api/v1/restore", json_body=payload)
            return bool(isinstance(res, dict) and (res.get("status") == "ok" or res.get("ok")))

        def archive_batch_to_local(
            self,
            queries: Optional[str | list[str]] = None,
            threads: Optional[str | list[str]] = None,
            messages: Optional[str | list[str]] = None,
        ) -> bool:
            payload: dict[str, Any] = {
                "queries": [queries] if isinstance(queries, str) else list(queries or []),
                "threads": [threads] if isinstance(threads, str) else list(threads or []),
                "messages": [messages] if isinstance(messages, str) else list(messages or []),
                "unmark": False,
            }
            res = self._request_json("POST", "/api/v1/move-archive", json_body=payload)
            return bool(isinstance(res, dict) and (res.get("status") == "ok" or res.get("ok")))

        def get_tags(self) -> list[dict[str, Any]]:
            data = self._request_json("GET", "/api/v1/tags")
            return list(data) if isinstance(data, list) else []

        def get_contacts(self, query: str = "") -> list[dict[str, Any]]:
            data = self._request_json("GET", "/api/v1/contacts", query_params={"q": query})
            return list(data) if isinstance(data, list) else []

        def send_message(self, account: str, message_bytes: bytes) -> tuple[bool, str]:
            data = self._request_json(
                "POST",
                "/api/v1/send",
                json_body={
                    "account": account,
                    "message_b64": base64.b64encode(message_bytes).decode("ascii"),
                },
            )
            if isinstance(data, dict):
                ok = data.get("status") == "ok" or bool(data.get("ok"))
                return ok, str(data.get("message") or "")
            return False, "Unknown send response"
