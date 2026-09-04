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
"""Python client library for NED (Notmuch Email Daemon).

Lightweight, zero external dependencies. Supports both local Unix domain socket
IPC and remote HTTP/HTTPS transports with Bearer authentication.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import json
import logging
import os
from pathlib import Path
import select
import socket
import sys
import threading
import time
from typing import Any, Callable, Iterator, Optional, Sequence, Union
import urllib.parse
import uuid

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class NedError(Exception):
    """Base exception for all NED client operations."""


class NedConnectionError(NedError):
    """Raised when connecting or communicating with the NED daemon fails."""


class NedResponseError(NedError):
    """Raised when the NED daemon returns an HTTP error status code."""

    def __init__(self, status: int, message: str, data: Any = None) -> None:
        super().__init__(f"NED error {status}: {message}")
        self.status = status
        self.message = message
        self.data = data


class NedAuthenticationError(NedResponseError):
    """Raised when access is rejected due to invalid or missing authentication."""


class NedNotFoundError(NedResponseError):
    """Raised when the requested resource (thread, part, message) is not found."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class NedEvent:
    """An event received from the NED Server-Sent Events (SSE) stream."""

    event: str = "message"
    data: dict[str, Any] = field(default_factory=dict)
    raw_data: str = ""
    id: Optional[str] = None
    scope: str = ""
    reason: Optional[str] = None
    target_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Low-level transport utilities
# ---------------------------------------------------------------------------


def resolve_default_socket_path() -> str:
    """Return canonical path for the NED Unix domain socket."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return str(Path(runtime_dir) / "ned" / "ned.sock")
    return str(Path(os.path.expanduser("~/.local/share/lazarus/ned")) / "ned.sock")


class UnixHTTPConnection(HTTPConnection):
    """Standard HTTPConnection that connects via a Unix domain stream socket."""

    def __init__(self, socket_path: str, timeout: Optional[float] = 30.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if self.timeout is not None and self.timeout > 0:
            sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        self.sock = sock


def _encode_multipart_form(
    fields: dict[str, str],
    attachments: list[tuple[str, str, bytes]],
) -> tuple[bytes, str]:
    """Encode fields and file attachments into a multipart/form-data payload."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []

    for name, value in fields.items():
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
        )
        parts.append(value.encode("utf-8"))
        parts.append(b"\r\n")

    for filename, content_type, data in attachments:
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        safe_name = filename.replace('"', '\\"')
        parts.append(
            f'Content-Disposition: form-data; name="attachment"; filename="{safe_name}"\r\n'.encode(
                "utf-8"
            )
        )
        parts.append(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        parts.append(data)
        parts.append(b"\r\n")

    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    payload = b"".join(parts)
    content_type_header = f"multipart/form-data; boundary={boundary}"
    return payload, content_type_header


# ---------------------------------------------------------------------------
# NedClient
# ---------------------------------------------------------------------------


class NedClient:
    """Client for Notmuch Email Daemon (NED).

    Communicates over Unix domain socket or HTTP/HTTPS with automatic
    serialization and error mapping.
    """

    def __init__(
        self,
        socket_path: Optional[Union[str, Path]] = None,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.timeout = timeout
        self.token = token or os.environ.get("NED_TOKEN")
        self.socket_path: Optional[str] = None
        self.base_url: Optional[str] = None

        if base_url:
            cleaned = base_url.strip()
            if cleaned.startswith("http+unix://"):
                cleaned = cleaned[len("http+unix://") :]
                self.socket_path = cleaned
            elif cleaned.startswith("unix://"):
                cleaned = cleaned[len("unix://") :]
                self.socket_path = cleaned
            else:
                self.base_url = cleaned.rstrip("/")
        elif socket_path:
            self.socket_path = str(socket_path)
        else:
            # Check environment variables before falling back to default socket
            env_sock = os.environ.get("NED_SOCK")
            env_url = os.environ.get("NED_URL")
            if env_sock:
                self.socket_path = env_sock
            elif env_url:
                self.base_url = env_url.rstrip("/")
            else:
                self.socket_path = resolve_default_socket_path()

    @classmethod
    def unix(
        cls,
        socket_path: Optional[Union[str, Path]] = None,
        timeout: float = 30.0,
    ) -> NedClient:
        """Create a client connecting via Unix domain socket."""
        path = socket_path or resolve_default_socket_path()
        return cls(socket_path=path, timeout=timeout)

    @classmethod
    def from_socket(
        cls,
        socket_path: Optional[Union[str, Path]] = None,
        timeout: float = 30.0,
    ) -> NedClient:
        """Alias for NedClient.unix."""
        return cls.unix(socket_path=socket_path, timeout=timeout)

    @classmethod
    def http(
        cls,
        base_url: str = "http://localhost:8080",
        token: Optional[str] = None,
        timeout: float = 30.0,
    ) -> NedClient:
        """Create a client connecting via HTTP/HTTPS with optional bearer token."""
        return cls(base_url=base_url, token=token, timeout=timeout)

    @classmethod
    def from_url(
        cls,
        base_url: str,
        token: Optional[str] = None,
        timeout: float = 30.0,
    ) -> NedClient:
        """Alias for NedClient.http."""
        return cls.http(base_url=base_url, token=token, timeout=timeout)

    def __enter__(self) -> NedClient:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        """Release client resources."""

    def _create_connection(
        self, timeout: Optional[float] = None
    ) -> HTTPConnection:
        """Create a new HTTPConnection instance for the configured transport."""
        effective_timeout = self.timeout if timeout is None else timeout
        if self.socket_path:
            return UnixHTTPConnection(self.socket_path, timeout=effective_timeout)

        if not self.base_url:
            raise NedConnectionError("No socket path or base URL configured")

        parsed = urllib.parse.urlsplit(self.base_url)
        host = parsed.hostname or "localhost"
        port = parsed.port

        if parsed.scheme == "https":
            return HTTPSConnection(
                host, port=port or 443, timeout=effective_timeout
            )
        return HTTPConnection(
            host, port=port or 80, timeout=effective_timeout
        )

    def _build_path(
        self, path: str, query_params: Optional[dict[str, Any]] = None
    ) -> str:
        """Build full request path including base URL prefix and query string."""
        prefix = ""
        if self.base_url:
            parsed = urllib.parse.urlsplit(self.base_url)
            prefix = parsed.path.rstrip("/")

        full_path = prefix + path
        if query_params:
            filtered = {k: v for k, v in query_params.items() if v is not None}
            if filtered:
                encoded = urllib.parse.urlencode(filtered)
                full_path = f"{full_path}?{encoded}" if "?" not in full_path else f"{full_path}&{encoded}"

        return full_path

    def _default_headers(self) -> dict[str, str]:
        """Build standard headers including authorization when configured."""
        headers = {
            "Accept": "application/json",
            "User-Agent": "Lazarus-NedClient/0.3",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        query_params: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Execute HTTP request and return status, headers, and raw response bytes."""
        full_path = self._build_path(path, query_params)
        req_headers = self._default_headers()
        if headers:
            req_headers.update(headers)

        conn = None
        try:
            conn = self._create_connection(timeout=timeout)
            conn.request(method, full_path, body=body, headers=req_headers)
            resp = conn.getresponse()
            status = resp.status
            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
            payload = resp.read()
        except (OSError, HTTPException) as exc:
            dest = self.socket_path or self.base_url
            raise NedConnectionError(f"Failed connecting to NED at {dest}: {exc}") from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        if status >= 400:
            err_msg = ""
            err_data: Any = None
            try:
                err_data = json.loads(payload.decode("utf-8"))
                if isinstance(err_data, dict):
                    err_msg = str(err_data.get("error") or err_data.get("message") or "")
            except Exception:
                err_msg = payload.decode("utf-8", errors="replace").strip()

            if not err_msg:
                err_msg = f"HTTP {status}"

            if status == 401:
                raise NedAuthenticationError(status, err_msg, err_data)
            if status == 404:
                raise NedNotFoundError(status, err_msg, err_data)
            raise NedResponseError(status, err_msg, err_data)

        return status, resp_headers, payload

    def _request_json(
        self,
        method: str,
        path: str,
        json_body: Any = None,
        query_params: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Execute request and parse returned body as JSON."""
        headers: dict[str, str] = {}
        body: Optional[bytes] = None

        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        _, _, raw_bytes = self._request(
            method=method,
            path=path,
            body=body,
            headers=headers,
            query_params=query_params,
            timeout=timeout,
        )

        if not raw_bytes:
            return None

        try:
            return json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise NedResponseError(
                200, f"Invalid JSON response from server: {exc}", raw_bytes
            ) from exc

    # -----------------------------------------------------------------------
    # API endpoints
    # -----------------------------------------------------------------------

    def ping(self) -> bool:
        """Check if NED daemon is reachable and responding."""
        try:
            self._request("GET", "/api/v1/ping", timeout=3.0)
            return True
        except NedError:
            return False

    def health(self) -> dict[str, Any]:
        """Fetch daemon health status."""
        data = self._request_json("GET", "/api/v1/ping")
        return data if isinstance(data, dict) else {}

    def search(
        self,
        query: str = "tag:inbox",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search threads matching a Notmuch query string."""
        params = {"q": query, "limit": limit, "offset": offset}
        data = self._request_json("GET", "/api/v1/threads", query_params=params)
        return data if isinstance(data, list) else []

    def get_thread(self, thread_id: str, full: bool = True) -> dict[str, Any]:
        """Fetch full thread hierarchy and messages by thread ID."""
        clean_id = urllib.parse.quote(thread_id, safe="")
        params = {"full": "true" if full else "false"}
        data = self._request_json("GET", f"/api/v1/threads/{clean_id}", query_params=params)
        if not isinstance(data, dict):
            raise NedResponseError(200, "Unexpected thread response format", data)
        return data

    def get_part(self, msg_id: str, part_id: int) -> bytes:
        """Retrieve raw payload bytes of a message body part or attachment."""
        clean_id = urllib.parse.quote(msg_id, safe="")
        _, _, payload = self._request(
            "GET", f"/api/v1/messages/{clean_id}/parts/{part_id}"
        )
        return payload

    def get_message(self, msg_id: str) -> dict[str, Any]:
        """Fetch one message's raw notmuch-show dict."""
        clean_id = urllib.parse.quote(msg_id, safe="")
        data = self._request_json("GET", f"/api/v1/messages/{clean_id}")
        if not isinstance(data, dict):
            raise NedResponseError(200, "Unexpected message response format", data)
        return data

    def get_part_data(self, msg_id: str, part_id: int) -> tuple[bytes, str, str]:
        """Retrieve part payload as ``(content, content_type, filename)``.

        Same tuple order as ``ned.service.get_part_data``.
        """
        clean_id = urllib.parse.quote(msg_id, safe="")
        _, headers, payload = self._request(
            "GET", f"/api/v1/messages/{clean_id}/parts/{part_id}"
        )

        content_type = headers.get("content-type", "application/octet-stream").split(";")[0].strip()
        filename = f"part-{part_id}"
        disp = headers.get("content-disposition", "")
        if "filename=" in disp:
            raw_fn = disp.split("filename=")[1].strip().strip('"').strip("'")
            if raw_fn:
                filename = raw_fn

        return payload, content_type, filename

    def modify_tags(
        self,
        queries: Union[Sequence[str], str] = (),
        add: Optional[Sequence[str]] = None,
        remove: Optional[Sequence[str]] = None,
        *,
        threads: Optional[Union[Sequence[str], str]] = None,
        messages: Optional[Union[Sequence[str], str]] = None,
        add_tags: Optional[Sequence[str]] = None,
        remove_tags: Optional[Sequence[str]] = None,
    ) -> bool:
        """Add or remove tags matching queries, threads, or messages."""
        q_list = [queries] if isinstance(queries, str) else list(queries or [])
        t_list = [threads] if isinstance(threads, str) else list(threads or [])
        m_list = [messages] if isinstance(messages, str) else list(messages or [])
        final_add = list(add or add_tags or [])
        final_remove = list(remove or remove_tags or [])

        payload: dict[str, Any] = {
            "add": final_add,
            "remove": final_remove,
        }
        if q_list:
            payload["queries"] = q_list
        if t_list:
            payload["threads"] = t_list
        if m_list:
            payload["messages"] = m_list
        res = self._request_json("POST", "/api/v1/tags", json_body=payload)
        return bool(isinstance(res, dict) and res.get("status") == "ok")

    def modify_thread_tags(
        self,
        thread_id: str,
        add: Optional[Sequence[str]] = None,
        remove: Optional[Sequence[str]] = None,
    ) -> bool:
        """Modify tags on a single thread via POST /api/v1/threads/{id}/tags."""
        clean_id = urllib.parse.unquote(thread_id).removeprefix("thread:")
        payload = {
            "add": list(add or []),
            "remove": list(remove or []),
        }
        res = self._request_json(
            "POST",
            f"/api/v1/threads/{urllib.parse.quote(clean_id, safe='')}/tags",
            json_body=payload,
        )
        return bool(isinstance(res, dict) and res.get("status") == "ok")

    def modify_message_tags(
        self,
        message_id: str,
        add: Optional[Sequence[str]] = None,
        remove: Optional[Sequence[str]] = None,
    ) -> bool:
        """Modify tags on a single message via POST /api/v1/messages/{id}/tags."""
        clean_id = urllib.parse.unquote(message_id).removeprefix("id:")
        if clean_id.startswith("<") and clean_id.endswith(">"):
            clean_id = clean_id[1:-1]
        payload = {
            "add": list(add or []),
            "remove": list(remove or []),
        }
        res = self._request_json(
            "POST",
            f"/api/v1/messages/{urllib.parse.quote(clean_id, safe='')}/tags",
            json_body=payload,
        )
        return bool(isinstance(res, dict) and res.get("status") == "ok")

    def search_messages(
        self,
        query: str,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[str]:
        """Search message IDs matching a Notmuch query string."""
        params = {"q": query, "limit": limit, "offset": offset}
        data = self._request_json("GET", "/api/v1/messages", query_params=params)
        return [str(m) for m in data] if isinstance(data, list) else []

    def count(self, query: str, output: str = "threads") -> int:
        """Return count of matching messages, threads, or files.

        Defaults to threads to mirror ``ned.notmuch.count`` so the desktop
        call sites (tab titles, marked-check) have identical semantics in
        NED and local modes.
        """
        res = self._request_json(
            "GET", "/api/v1/count", query_params={"q": query, "output": output}
        )
        if isinstance(res, dict) and "count" in res:
            return int(res["count"])
        return 0

    def count_batch(
        self, queries: Sequence[str], output: str = "threads"
    ) -> list[int]:
        """Return counts for a batch of queries."""
        payload = {"queries": list(queries), "output": output}
        res = self._request_json("POST", "/api/v1/count", json_body=payload)
        if isinstance(res, dict) and "counts" in res and isinstance(res["counts"], list):
            return [int(c) for c in res["counts"]]
        return [0] * len(queries)

    def archive_thread(self, thread_or_query: Union[list[str], str]) -> bool:
        """Archive a thread or query (removes inbox/unread and moves Maildir files)."""
        if isinstance(thread_or_query, list):
            res = self._request_json(
                "POST", "/api/v1/threads/archive", json_body={"queries": thread_or_query}
            )
            return bool(isinstance(res, dict) and res.get("status") == "ok")
        clean = thread_or_query.strip()
        if " " in clean or ":" in clean:
            res = self._request_json(
                "POST", "/api/v1/threads/archive", json_body={"query": clean}
            )
        else:
            clean_id = urllib.parse.quote(clean, safe="")
            res = self._request_json("POST", f"/api/v1/threads/{clean_id}/archive")
        return bool(isinstance(res, dict) and res.get("status") == "ok")

    def unarchive_thread(self, thread_or_query: Union[list[str], str]) -> bool:
        """Restore an archived thread or query back to inbox."""
        if isinstance(thread_or_query, list):
            res = self._request_json(
                "POST", "/api/v1/threads/unarchive", json_body={"queries": thread_or_query}
            )
            return bool(isinstance(res, dict) and res.get("status") == "ok")
        clean = thread_or_query.strip()
        if " " in clean or ":" in clean:
            res = self._request_json(
                "POST", "/api/v1/threads/unarchive", json_body={"query": clean}
            )
        else:
            clean_id = urllib.parse.quote(clean, safe="")
            res = self._request_json("POST", f"/api/v1/threads/{clean_id}/unarchive")
        return bool(isinstance(res, dict) and res.get("status") == "ok")

    def trash_thread(self, thread_or_query: Union[list[str], str]) -> bool:
        """Move a thread or query to trash folder and tag as trash."""
        if isinstance(thread_or_query, list):
            res = self._request_json(
                "POST", "/api/v1/threads/trash", json_body={"queries": thread_or_query}
            )
            return bool(isinstance(res, dict) and res.get("status") == "ok")
        clean = thread_or_query.strip()
        if " " in clean or ":" in clean:
            res = self._request_json(
                "POST", "/api/v1/threads/trash", json_body={"query": clean}
            )
        else:
            clean_id = urllib.parse.quote(clean, safe="")
            res = self._request_json("POST", f"/api/v1/threads/{clean_id}/trash")
        return bool(isinstance(res, dict) and res.get("status") == "ok")

    def untrash_thread(self, thread_or_query: Union[list[str], str]) -> bool:
        """Restore a thread or query from trash back to inbox."""
        if isinstance(thread_or_query, list):
            res = self._request_json(
                "POST", "/api/v1/threads/untrash", json_body={"queries": thread_or_query}
            )
            return bool(isinstance(res, dict) and res.get("status") == "ok")
        clean = thread_or_query.strip()
        if " " in clean or ":" in clean:
            res = self._request_json(
                "POST", "/api/v1/threads/untrash", json_body={"query": clean}
            )
        else:
            clean_id = urllib.parse.quote(clean, safe="")
            res = self._request_json("POST", f"/api/v1/threads/{clean_id}/untrash")
        return bool(isinstance(res, dict) and res.get("status") == "ok")

    def expunge_trash(self) -> int:
        """Flag every file matching ``tag:trash`` with the Maildir T flag.

        Irreversible. Returns the number of files newly flagged.
        """
        res = self._request_json("POST", "/api/v1/expunge")
        if isinstance(res, dict) and res.get("status") == "ok":
            return int(res.get("tagged") or 0)
        return 0

    def apply_filter_rules(self) -> int:
        """Apply configured filter rules daemon-side; returns matches."""
        res = self._request_json("POST", "/api/v1/rules")
        if isinstance(res, dict) and res.get("status") == "ok":
            return int(res.get("matched") or 0)
        return 0

    def index_new(self) -> bool:
        """Ask the daemon to run ``notmuch new --no-hooks``.

        Used after sent-mail appends so new files are indexed without
        waiting for the next sync cycle.
        """
        res = self._request_json("POST", "/api/v1/index")
        return bool(isinstance(res, dict) and res.get("status") == "ok")

    def toggle_flag(self, thread_id: str, flag: bool = True) -> bool:
        """Toggle star/flag status on a thread."""
        clean_id = urllib.parse.quote(thread_id, safe="")
        res = self._request_json(
            "POST", f"/api/v1/threads/{clean_id}/star", json_body={"flag": flag}
        )
        return bool(isinstance(res, dict) and res.get("status") == "ok")

    def star_thread(self, thread_id: str, star: bool = True) -> bool:
        """Alias for toggle_flag."""
        return self.toggle_flag(thread_id, flag=star)

    def get_tags(self) -> list[dict[str, Any]]:
        """Fetch all known tags with message or thread counts."""
        data = self._request_json("GET", "/api/v1/tags")
        return data if isinstance(data, list) else []

    def get_contacts(self, query: str = "") -> list[dict[str, str]]:
        """Query address autocomplete matching prefix or substring."""
        params = {"q": query} if query else {}
        data = self._request_json("GET", "/api/v1/contacts", query_params=params)
        return data if isinstance(data, list) else []

    def get_reply_seed(self, msg_id: str, to_all: bool = False) -> dict[str, Any]:
        """Generate reply header seed, quoted body, and signature for a message."""
        clean_id = urllib.parse.quote(msg_id, safe="")
        params = {"id": msg_id, "to_all": "true" if to_all else "false"}
        data = self._request_json(
            "GET", f"/api/v1/messages/{clean_id}/reply-seed", query_params=params
        )
        return data if isinstance(data, dict) else {}

    def get_accounts(self) -> list[str]:
        """Fetch list of configured SMTP sender account identifiers."""
        data = self._request_json("GET", "/api/v1/accounts")
        return list(data.get("accounts", [])) if isinstance(data, dict) else []

    def get_signatures(self) -> dict[str, str]:
        """Fetch mapping of account identifiers to their email signature text."""
        data = self._request_json("GET", "/api/v1/signatures")
        return dict(data.get("signatures", {})) if isinstance(data, dict) else {}

    def get_accounts_detail(self) -> dict[str, Any]:
        """Fetch accounts plus per-account mail identity for compose.

        :returns: ``{'accounts': [...], 'email': {acct: addr},
        'gnupg_keyid': {acct: key | None}}`` (empty dicts on failure).
        """
        data = self._request_json("GET", "/api/v1/accounts")
        if not isinstance(data, dict):
            return {'accounts': [], 'email': {}, 'gnupg_keyid': {}}
        return {
            'accounts': list(data.get('accounts', [])),
            'email': dict(data.get('email', {})),
            'gnupg_keyid': dict(data.get('gnupg_keyid', {})),
        }

    def get_signatures_detail(self) -> dict[str, Any]:
        """Fetch signatures including HTML per account.

        :returns: ``{'use_signature': bool, 'signatures': {acct: text},
        'signatures_html': {acct: html}}``.
        """
        data = self._request_json("GET", "/api/v1/signatures")
        if not isinstance(data, dict):
            return {'use_signature': True, 'signatures': {}, 'signatures_html': {}}
        return {
            'use_signature': bool(data.get('use_signature', True)),
            'signatures': dict(data.get('signatures', {})),
            'signatures_html': dict(data.get('signatures_html', {})),
        }

    def send_message(self, account: str, message_bytes: bytes) -> tuple[bool, str]:
        """Send a fully built RFC822 message via NED.

        The message is base64-encoded in JSON; the daemon pipes it to the
        account's msmtp command, saves the sent copy, and indexes it.
        """
        data = self._request_json(
            "POST", "/api/v1/send",
            json_body={
                "account": account,
                "message_b64": base64.b64encode(message_bytes).decode("ascii"),
            },
        )
        if isinstance(data, dict):
            ok = data.get("status") == "ok"
            return ok, str(data.get("message") or "")
        return False, "Unknown send response"

    def sync_mail(self) -> tuple[bool, str]:
        """Trigger background mail synchronization (mbsync + notmuch new)."""
        res = self._request_json("POST", "/api/v1/sync")
        if isinstance(res, dict):
            ok = res.get("status") == "ok"
            msg = str(res.get("message") or "")
            return ok, msg
        return False, "Unknown sync response"

    def send_email(
        self,
        account: str,
        to: Union[list[str], str],
        cc: Optional[Union[list[str], str]] = None,
        bcc: Optional[Union[list[str], str]] = None,
        subject: str = "",
        body_text: str = "",
        in_reply_to: str = "",
        references: str = "",
        attachments: Optional[list[tuple[str, str, bytes]]] = None,
    ) -> tuple[bool, str]:
        """Send an outbound email via NED.

        Uses multipart encoding when attachments are provided, and standard
        JSON when sending text only.
        """
        to_list = [to] if isinstance(to, str) else list(to)
        cc_list = [cc] if isinstance(cc, str) else (list(cc) if cc else [])
        bcc_list = [bcc] if isinstance(bcc, str) else (list(bcc) if bcc else [])

        if attachments:
            fields = {
                "account": account,
                "to": ", ".join(to_list),
                "cc": ", ".join(cc_list),
                "bcc": ", ".join(bcc_list),
                "subject": subject,
                "body_text": body_text,
                "in_reply_to": in_reply_to,
                "references": references,
            }
            body_bytes, ctype = _encode_multipart_form(fields, attachments)
            headers = {"Content-Type": ctype}
            _, _, resp_bytes = self._request(
                "POST", "/api/v1/send", body=body_bytes, headers=headers
            )
            try:
                res = json.loads(resp_bytes.decode("utf-8"))
                return (
                    isinstance(res, dict) and res.get("status") == "ok",
                    str(res.get("message") or ""),
                )
            except Exception:
                return True, "Email sent"
        else:
            payload = {
                "account": account,
                "to": to_list,
                "cc": cc_list,
                "bcc": bcc_list,
                "subject": subject,
                "body_text": body_text,
                "in_reply_to": in_reply_to,
                "references": references,
            }
            res = self._request_json("POST", "/api/v1/send", json_body=payload)
            if isinstance(res, dict):
                return res.get("status") == "ok", str(res.get("message") or "")
            return False, "Unknown send response"

    def listen_events(
        self,
        stop_event: Optional[threading.Event] = None,
        timeout: Optional[float] = None,
    ) -> Iterator[NedEvent]:
        """Subscribe to the SSE invalidation event stream.

        Yields NedEvent objects as they arrive from the daemon. Automatically
        handles keepalive ping comments and event framing.
        """
        conn = None
        try:
            conn = self._create_connection(timeout=timeout)
            headers = self._default_headers()
            headers["Accept"] = "text/event-stream"
            conn.request("GET", self._build_path("/api/v1/events"), headers=headers)
            resp = conn.getresponse()

            if resp.status != 200:
                err_body = resp.read().decode("utf-8", errors="replace")
                raise NedResponseError(
                    resp.status, f"SSE subscription failed: {resp.status} - {err_body}"
                )

            event_name = "message"
            data_lines: list[str] = []
            event_id: Optional[str] = None

            raw_sock = None
            try:
                if hasattr(resp, "fp") and hasattr(resp.fp, "raw") and hasattr(resp.fp.raw, "_sock"):
                    raw_sock = resp.fp.raw._sock
                elif hasattr(resp, "sock"):
                    raw_sock = resp.sock
            except Exception:
                raw_sock = None

            if raw_sock is not None:
                raw_sock.setblocking(False)
                buf = b""
                while True:
                    if stop_event is not None and stop_event.is_set():
                        break
                    r, _, _ = select.select([raw_sock], [], [], 0.2)
                    if not r:
                        continue
                    try:
                        chunk = raw_sock.recv(4096)
                    except BlockingIOError:
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line_bytes, buf = buf.split(b"\n", 1)
                        line = line_bytes.decode("utf-8", errors="replace").rstrip("\r")

                        if not line:
                            if data_lines:
                                data_str = "\n".join(data_lines)
                                parsed: dict[str, Any] = {}
                                try:
                                    loaded = json.loads(data_str)
                                    if isinstance(loaded, dict):
                                        parsed = loaded
                                except Exception:
                                    pass

                                yield NedEvent(
                                    event=event_name,
                                    data=parsed,
                                    raw_data=data_str,
                                    id=event_id,
                                    scope=str(parsed.get("scope", "")),
                                    reason=parsed.get("reason"),
                                    target_id=parsed.get("id"),
                                )
                                event_name = "message"
                                data_lines = []
                                event_id = None
                            continue

                        if line.startswith(":"):
                            continue
                        if line.startswith("event:"):
                            event_name = line[len("event:") :].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[len("data:") :].strip())
                        elif line.startswith("id:"):
                            event_id = line[len("id:") :].strip()
            else:
                while True:
                    if stop_event is not None and stop_event.is_set():
                        break

                    line_bytes = resp.readline()
                    if not line_bytes:
                        break

                    line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")

                    if not line:
                        if data_lines:
                            data_str = "\n".join(data_lines)
                            parsed_mock: dict[str, Any] = {}
                            try:
                                loaded_mock = json.loads(data_str)
                                if isinstance(loaded_mock, dict):
                                    parsed_mock = loaded_mock
                            except Exception:
                                pass

                            yield NedEvent(
                                event=event_name,
                                data=parsed_mock,
                                raw_data=data_str,
                                id=event_id,
                                scope=str(parsed_mock.get("scope", "")),
                                reason=parsed_mock.get("reason"),
                                target_id=parsed_mock.get("id"),
                            )
                            event_name = "message"
                            data_lines = []
                            event_id = None
                        continue

                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_name = line[len("event:") :].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[len("data:") :].strip())
                    elif line.startswith("id:"):
                        event_id = line[len("id:") :].strip()

        except (OSError, HTTPException) as exc:
            if stop_event is not None and stop_event.is_set():
                return
            dest = self.socket_path or self.base_url
            raise NedConnectionError(f"SSE stream error from {dest}: {exc}") from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def watch_events(
        self,
        on_event: Callable[[NedEvent], None],
        on_error: Optional[Callable[[Exception], None]] = None,
        stop_event: Optional[threading.Event] = None,
        reconnect: bool = True,
        reconnect_delay: float = 1.0,
    ) -> threading.Thread:
        """Start a daemon thread that consumes the SSE event stream.

        Invokes on_event for each event received. Reconnects on network loss
        until stop_event is set.
        """
        active_stop = stop_event or threading.Event()

        def _worker() -> None:
            while not active_stop.is_set():
                try:
                    for ev in self.listen_events(stop_event=active_stop):
                        if active_stop.is_set():
                            break
                        try:
                            on_event(ev)
                        except Exception as cb_exc:
                            logger.exception("Error in SSE event callback: %s", cb_exc)
                except Exception as exc:
                    if active_stop.is_set():
                        break
                    if on_error:
                        try:
                            on_error(exc)
                        except Exception:
                            pass
                    if not reconnect:
                        break
                    time.sleep(reconnect_delay)

        thread = threading.Thread(target=_worker, daemon=True, name="NedEventWatcher")
        thread.start()
        return thread


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(args: Optional[list[str]] = None) -> int:
    """Command line interface for querying and interacting with NED."""
    parser = argparse.ArgumentParser(
        description="Notmuch Email Daemon (NED) client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--socket",
        help="Path to NED Unix domain socket (default: XDG_RUNTIME_DIR/ned/ned.sock)",
    )
    parser.add_argument(
        "--url",
        help="Base URL of remote NED server (e.g. http://100.x.y.z:8080)",
    )
    parser.add_argument(
        "--token",
        help="Bearer authentication token for remote server",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ping
    subparsers.add_parser("ping", help="Test daemon connectivity")

    # health
    subparsers.add_parser("health", help="Check daemon health")

    # search
    p_search = subparsers.add_parser("search", help="Search threads")
    p_search.add_argument("query", nargs="?", default="tag:inbox", help="Notmuch query")
    p_search.add_argument("--limit", type=int, default=20, help="Maximum threads to return")
    p_search.add_argument("--offset", type=int, default=0, help="Thread offset")

    # thread
    p_thread = subparsers.add_parser("thread", help="View thread messages")
    p_thread.add_argument("thread_id", help="Thread ID")

    # tags
    subparsers.add_parser("tags", help="List all tags and counts")

    # contacts
    p_contacts = subparsers.add_parser("contacts", help="Search address book")
    p_contacts.add_argument("query", nargs="?", default="", help="Search substring")

    # sync
    subparsers.add_parser("sync", help="Trigger mail synchronization")

    # events
    subparsers.add_parser("events", help="Stream real-time invalidation events")

    parsed_args = parser.parse_args(args)

    client = NedClient(
        socket_path=parsed_args.socket,
        base_url=parsed_args.url,
        token=parsed_args.token,
    )

    try:
        cmd = parsed_args.command
        if cmd == "ping":
            ok = client.ping()
            if ok:
                print("NED is reachable and responding.")
                return 0
            print("NED is not responding.", file=sys.stderr)
            return 1

        elif cmd == "health":
            data = client.health()
            print(json.dumps(data, indent=2))
            return 0

        elif cmd == "search":
            threads = client.search(
                query=parsed_args.query,
                limit=parsed_args.limit,
                offset=parsed_args.offset,
            )
            print(json.dumps(threads, indent=2))
            return 0

        elif cmd == "thread":
            thread = client.get_thread(parsed_args.thread_id)
            print(json.dumps(thread, indent=2))
            return 0

        elif cmd == "tags":
            tags = client.get_tags()
            print(json.dumps(tags, indent=2))
            return 0

        elif cmd == "contacts":
            contacts = client.get_contacts(query=parsed_args.query)
            print(json.dumps(contacts, indent=2))
            return 0

        elif cmd == "sync":
            ok, msg = client.sync_mail()
            if ok:
                print(f"Sync succeeded: {msg}")
                return 0
            print(f"Sync failed: {msg}", file=sys.stderr)
            return 1

        elif cmd == "events":
            print("Listening for SSE events (Ctrl+C to quit)...")
            for ev in client.listen_events():
                print(f"[{ev.event}] scope={ev.scope} id={ev.target_id} reason={ev.reason} data={ev.raw_data}")
            return 0

    except KeyboardInterrupt:
        return 0
    except NedError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
