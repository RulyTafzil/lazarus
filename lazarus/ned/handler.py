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
"""Unified HTTP/REST request handler for NED over Unix sockets and TCP.

Supports versioned /api/v1/ endpoints, legacy /api/ aliases, Server-Sent Events,
serialized mutations, and static asset serving.
"""

from __future__ import annotations

import email
import email.policy
from http import HTTPStatus
import http.server
import json
import logging
import mimetypes
import os
from pathlib import Path
import queue
import re
from typing import Any
import urllib.parse

from .. import settings
from ..server import service
from .concurrency import mutation_lock
from .events import broadcaster

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "server" / "static"


class NedRequestHandler(http.server.BaseHTTPRequestHandler):
    """Handles REST API, SSE, and static asset requests for NED."""

    server_version = "NED/0.1"

    def address_string(self) -> str:
        """Return safe peer address, handling Unix stream sockets cleanly."""
        if isinstance(self.client_address, (tuple, list)) and len(self.client_address) > 0:
            return str(self.client_address[0])
        return "unix"

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _is_unix_socket(self) -> bool:
        return self.address_string() == "unix"

    def _check_auth(self) -> bool:
        """Validate bearer token for TCP connections. Unix socket relies on OS permissions."""
        if self._is_unix_socket():
            return True

        token = getattr(settings, "web_token", "").strip()
        if not token:
            return True

        # Check Authorization header
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            if auth_hdr[7:].strip() == token:
                return True

        # Check query parameter token
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if "token" in qs and qs["token"][0] == token:
            return True

        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("WWW-Authenticate", 'Bearer realm="NED"')
        self.end_headers()
        self.wfile.write(json.dumps({"error": "Unauthorized"}).encode("utf-8"))
        return False

    def send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        """Write JSON response with standard non-caching headers."""
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(payload)

    def send_error_json(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        """Write error JSON response."""
        self.send_json({"error": message}, status=status)

    def _read_body(self) -> bytes:
        """Read full request payload."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _read_json_body(self) -> dict[str, Any]:
        """Read and parse JSON body."""
        raw = self._read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _normalize_path(self, path: str) -> str:
        """Normalize legacy /api/... routes to /api/v1/..."""
        if path.startswith("/api/") and not path.startswith("/api/v1/"):
            return "/api/v1/" + path[len("/api/") :]
        return path

    def do_GET(self) -> None:
        """Route GET requests."""
        if not self._check_auth():
            return

        parsed = urllib.parse.urlparse(self.path)
        path = self._normalize_path(parsed.path)
        qs = urllib.parse.parse_qs(parsed.query)

        # Root and static files
        if path == "/" or path == "/index.html":
            self._serve_static_file(STATIC_DIR / "index.html")
            return
        if path.startswith("/static/"):
            rel_path = path.removeprefix("/static/").lstrip("/")
            target = (STATIC_DIR / rel_path).resolve()
            if target.is_relative_to(STATIC_DIR) and target.is_file():
                self._serve_static_file(target)
            else:
                self.send_error_json("File not found", HTTPStatus.NOT_FOUND)
            return

        # Server-Sent Events (SSE) stream
        if path == "/api/v1/events":
            self._handle_events_stream()
            return

        # Health / Ping
        if path in ("/api/v1/ping", "/api/v1/health"):
            self.send_json({"status": "ok", "service": "ned", "version": "1.0"})
            return

        # API Routes
        if path in ("/api/v1/search", "/api/v1/threads"):
            query = qs.get("q", ["tag:inbox"])[0]
            limit = int(qs.get("limit", ["50"])[0])
            offset = int(qs.get("offset", ["0"])[0])
            threads = service.search_threads(query, limit=limit, offset=offset)
            self.send_json(threads)
            return

        m_thread = re.match(r"^/api/v1/threads/([^/]+)$", path)
        if m_thread:
            thread_id = urllib.parse.unquote(m_thread.group(1))
            thread_data = service.get_thread_messages(thread_id)
            if thread_data is None:
                self.send_error_json("Thread not found", HTTPStatus.NOT_FOUND)
            else:
                self.send_json(thread_data)
            return

        m_part = re.match(r"^/api/v1/messages/([^/]+)/parts?/([^/]+)$", path)
        if m_part:
            msg_id = urllib.parse.unquote(m_part.group(1))
            part_id = int(m_part.group(2))
            content, filename, content_type = service.get_part_data(msg_id, part_id)
            if content is None:
                self.send_error_json("Part not found", HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            if filename:
                safe_name = filename.replace('"', '\\"')
                self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
            self.end_headers()
            self.wfile.write(content)
            return

        if path == "/api/v1/tags":
            self.send_json(service.get_all_tags())
            return

        if path == "/api/v1/contacts":
            q = qs.get("q", [""])[0]
            matches = service.get_contacts(q)
            self.send_json(matches)
            return

        if path == "/api/v1/reply-seed" or re.match(r"^/api/v1/messages/([^/]+)/reply-seed$", path):
            m_seed = re.match(r"^/api/v1/messages/([^/]+)/reply-seed$", path)
            if m_seed:
                msg_id = urllib.parse.unquote(m_seed.group(1))
            else:
                msg_id = qs.get("id", [""])[0]
            to_all = qs.get("to_all", ["false"])[0].lower() in ("true", "1", "yes")
            seed = service.get_reply_seed(msg_id, to_all=to_all)
            if seed is None:
                self.send_error_json("Message not found", HTTPStatus.NOT_FOUND)
            else:
                self.send_json(seed)
            return

        if path == "/api/v1/accounts":
            accts = list(settings.smtp_accounts.keys()) if isinstance(settings.smtp_accounts, dict) else list(settings.smtp_accounts)
            self.send_json(accts)
            return

        if path == "/api/v1/signatures":
            self.send_json(service.get_signatures())
            return

        self.send_error_json("Not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        """Route POST requests."""
        if not self._check_auth():
            return

        parsed = urllib.parse.urlparse(self.path)
        path = self._normalize_path(parsed.path)

        # Tag modification
        if path in ("/api/v1/tag", "/api/v1/tags"):
            body = self._read_json_body()
            queries = body.get("queries", [])
            query = body.get("query")
            if query and not queries:
                queries = [query]
            add_tags = body.get("add", [])
            remove_tags = body.get("remove", [])
            if not queries or (not add_tags and not remove_tags):
                self.send_error_json("queries and add/remove tags required")
                return
            with mutation_lock:
                ok = service.modify_tags(queries, add_tags=add_tags, remove_tags=remove_tags)
            if ok:
                broadcaster.broadcast_invalidate("threads", reason="tag")
                self.send_json({"status": "ok"})
            else:
                self.send_error_json("Failed modifying tags", HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # Archive thread
        m_archive = re.match(r"^/api/v1/threads/([^/]+)/archive$", path)
        if m_archive:
            thread_id = urllib.parse.unquote(m_archive.group(1))
            with mutation_lock:
                ok = service.archive_thread(thread_id)
            if ok:
                broadcaster.broadcast_invalidate("thread", thread_id, reason="archive")
                broadcaster.broadcast_invalidate("threads", reason="archive")
                self.send_json({"status": "ok", "archived": thread_id})
            else:
                self.send_error_json("Failed archiving thread", HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # Unarchive thread
        m_unarchive = re.match(r"^/api/v1/threads/([^/]+)/unarchive$", path)
        if m_unarchive:
            thread_id = urllib.parse.unquote(m_unarchive.group(1))
            with mutation_lock:
                ok = service.unarchive_thread(thread_id)
            if ok:
                broadcaster.broadcast_invalidate("thread", thread_id, reason="unarchive")
                broadcaster.broadcast_invalidate("threads", reason="unarchive")
                self.send_json({"status": "ok", "unarchived": thread_id})
            else:
                self.send_error_json("Failed unarchiving thread", HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # Trash thread
        m_trash = re.match(r"^/api/v1/threads/([^/]+)/trash$", path)
        if m_trash:
            thread_id = urllib.parse.unquote(m_trash.group(1))
            with mutation_lock:
                ok = service.trash_thread(thread_id)
            if ok:
                broadcaster.broadcast_invalidate("thread", thread_id, reason="trash")
                broadcaster.broadcast_invalidate("threads", reason="trash")
                self.send_json({"status": "ok", "trashed": thread_id})
            else:
                self.send_error_json("Failed trashing thread", HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # Untrash thread
        m_untrash = re.match(r"^/api/v1/threads/([^/]+)/untrash$", path)
        if m_untrash:
            thread_id = urllib.parse.unquote(m_untrash.group(1))
            with mutation_lock:
                ok = service.untrash_thread(thread_id)
            if ok:
                broadcaster.broadcast_invalidate("thread", thread_id, reason="untrash")
                broadcaster.broadcast_invalidate("threads", reason="untrash")
                self.send_json({"status": "ok", "untrashed": thread_id})
            else:
                self.send_error_json("Failed restoring thread from trash", HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # Flag / star thread
        m_star = re.match(r"^/api/v1/threads/([^/]+)/(?:star|flag)$", path)
        if m_star:
            thread_id = urllib.parse.unquote(m_star.group(1))
            body = self._read_json_body()
            flag = bool(body.get("flag", True))
            with mutation_lock:
                ok = service.toggle_flag(thread_id, flag=flag)
            if ok:
                broadcaster.broadcast_invalidate("thread", thread_id, reason="star")
                broadcaster.broadcast_invalidate("threads", reason="star")
                self.send_json({"status": "ok", "starred": flag, "flag": flag})
            else:
                self.send_error_json("Failed toggling flag", HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # Mail synchronization
        if path == "/api/v1/sync":
            with mutation_lock:
                ok, msg = service.sync_mail()
            if ok:
                broadcaster.broadcast_invalidate("threads", reason="sync")
                self.send_json({"status": "ok", "message": msg})
            else:
                self.send_error_json(f"Sync failed: {msg}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # Send email
        if path == "/api/v1/send":
            self._handle_send()
            return

        self.send_error_json("Not found", HTTPStatus.NOT_FOUND)

    def _handle_send(self) -> None:
        """Handle multipart form or JSON submission for email send."""
        ctype = self.headers.get("Content-Type", "")
        account = ""
        to: list[str] = []
        cc: list[str] = []
        bcc: list[str] = []
        subject = ""
        body_text = ""
        in_reply_to = ""
        references = ""
        attachments: list[tuple[str, str, bytes]] = []

        if ctype.startswith("multipart/form-data"):
            raw_body = self._read_body()
            header_block = f"Content-Type: {ctype}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
            full_msg = email.message_from_bytes(header_block + raw_body, policy=email.policy.default)
            fields: dict[str, str] = {}

            for part in full_msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                filename = part.get_filename()
                if filename:
                    raw_payload = part.get_payload(decode=True)
                    payload_bytes = raw_payload if isinstance(raw_payload, bytes) else b""
                    part_ctype = part.get_content_type()
                    attachments.append((str(filename), part_ctype, payload_bytes))
                elif isinstance(name, str):
                    val = part.get_content()
                    if isinstance(val, str):
                        fields[name] = val
                    elif isinstance(val, bytes):
                        fields[name] = val.decode("utf-8", errors="replace")

            account = fields.get("account", "")
            to = [x.strip() for x in fields.get("to", "").split(",") if x.strip()]
            cc = [x.strip() for x in fields.get("cc", "").split(",") if x.strip()]
            bcc = [x.strip() for x in fields.get("bcc", "").split(",") if x.strip()]
            subject = fields.get("subject", "")
            body_text = fields.get("body_text", "") or fields.get("body", "")
            in_reply_to = fields.get("in_reply_to", "")
            references = fields.get("references", "")
        else:
            body_json = self._read_json_body()
            account = body_json.get("account", "")
            raw_to = body_json.get("to", [])
            to = [x.strip() for x in raw_to.split(",") if x.strip()] if isinstance(raw_to, str) else raw_to
            raw_cc = body_json.get("cc", [])
            cc = [x.strip() for x in raw_cc.split(",") if x.strip()] if isinstance(raw_cc, str) else raw_cc
            raw_bcc = body_json.get("bcc", [])
            bcc = [x.strip() for x in raw_bcc.split(",") if x.strip()] if isinstance(raw_bcc, str) else raw_bcc
            subject = body_json.get("subject", "")
            body_text = body_json.get("body_text", "") or body_json.get("body", "")
            in_reply_to = body_json.get("in_reply_to", "")
            references = body_json.get("references", "")

        if not account or not to:
            self.send_error_json("Account and To fields are required")
            return

        with mutation_lock:
            ok, err_or_msg = service.send_email(
                account=account,
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                body_text=body_text,
                in_reply_to=in_reply_to,
                references=references,
                attachments=attachments,
            )

        if ok:
            broadcaster.broadcast_invalidate("threads", reason="send")
            self.send_json({"status": "ok", "message": err_or_msg})
        else:
            self.send_error_json(f"Send failed: {err_or_msg}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_events_stream(self) -> None:
        """Handle Server-Sent Events (SSE) streaming connection."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        q = broadcaster.subscribe()
        try:
            # Send initial connection comment
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()

            # Keep connection open and stream events
            while True:
                try:
                    data = q.get(timeout=15.0)
                    self.wfile.write(data)
                    self.wfile.flush()
                except queue.Empty:
                    # Keepalive ping to detect broken client connections
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            logger.debug("SSE subscriber disconnected (%s)", self.address_string())
        finally:
            broadcaster.unsubscribe(q)

    def _serve_static_file(self, path: Path) -> None:
        """Serve static file content with no-cache headers."""
        if not path.is_file():
            self.send_error_json("File not found", HTTPStatus.NOT_FOUND)
            return

        ctype, _ = mimetypes.guess_type(str(path))
        if path.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif path.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif path.suffix == ".html":
            ctype = "text/html; charset=utf-8"

        try:
            with open(path, "rb") as f:
                content = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(content)
        except OSError as e:
            logger.warning("Error reading %s: %s", path, e)
            self.send_error_json("Error reading file", HTTPStatus.INTERNAL_SERVER_ERROR)
