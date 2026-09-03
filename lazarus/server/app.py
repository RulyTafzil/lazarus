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
"""HTTP request router and server for the mobile web interface.

Built using standard library http.server and socketserver with zero
external dependencies.
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
import re
import socketserver
from typing import Any
import urllib.parse

from .. import settings
from . import service

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / 'static'


class LazarusRequestHandler(http.server.BaseHTTPRequestHandler):
    """Handles REST API and static asset requests for mobile email."""

    server_version = "LazarusWeb/0.3"

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - - [%s] %s", self.client_address[0], self.log_date_time_string(), format % args)

    def _check_auth(self) -> bool:
        """Validate bearer token if web_token is configured."""
        token = getattr(settings, 'web_token', '').strip()
        if not token:
            return True

        # Check Authorization header
        auth_hdr = self.headers.get('Authorization', '')
        if auth_hdr.startswith('Bearer '):
            if auth_hdr[7:].strip() == token:
                return True

        # Check query parameters
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if 'token' in qs and qs['token'][0] == token:
            return True

        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('WWW-Authenticate', 'Bearer realm="Lazarus"')
        self.end_headers()
        self.wfile.write(json.dumps({'error': 'Unauthorized'}).encode('utf-8'))
        return False

    def send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        """Write JSON response with standard headers."""
        payload = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.end_headers()
        self.wfile.write(payload)

    def send_error_json(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        """Write error JSON response."""
        self.send_json({'error': message}, status=status)

    def _read_body(self) -> bytes:
        """Read full request payload."""
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if length <= 0:
            return b''
        return self.rfile.read(length)

    def _read_json(self) -> dict[str, Any]:
        """Read and parse incoming JSON payload."""
        data = self._read_body()
        if not data:
            return {}
        try:
            parsed = json.loads(data.decode('utf-8', errors='replace'))
            if isinstance(parsed, dict):
                return parsed
            return {}
        except Exception:
            return {}

    # -----------------------------------------------------------------------
    # Route Handlers: GET
    # -----------------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        # Static assets
        if path == '/' or path == '/index.html':
            self._serve_static_file('index.html')
            return
        if path.startswith('/static/'):
            filename = path.removeprefix('/static/').strip('/')
            self._serve_static_file(filename)
            return

        # Authentication check for API endpoints
        if not self._check_auth():
            return

        # /api/search?q=...&limit=...&offset=...
        if path == '/api/search':
            q = qs.get('q', ['tag:inbox'])[0]
            limit = int(qs.get('limit', ['50'])[0])
            offset = int(qs.get('offset', ['0'])[0])
            threads = service.search_threads(q, limit=limit, offset=offset)
            self.send_json(threads)
            return

        # /api/threads/{thread_id}
        m_thread = re.fullmatch(r'/api/threads/([^/]+)', path)
        if m_thread:
            thread_id = m_thread.group(1)
            data = service.get_thread_messages(thread_id)
            self.send_json(data)
            return

        # /api/messages/{message_id}/parts/{part_id}
        m_part = re.fullmatch(r'/api/messages/([^/]+)/parts/(\d+)', path)
        if m_part:
            msg_id = m_part.group(1)
            part_id = int(m_part.group(2))
            raw_bytes, content_type, filename = service.get_part_data(msg_id, part_id)
            self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw_bytes)))
            safe_name = filename.replace('"', '_')
            self.send_header('Content-Disposition', f'inline; filename="{safe_name}"')
            self.end_headers()
            self.wfile.write(raw_bytes)
            return

        # /api/messages/{message_id}/reply-seed?to_all=...
        m_seed = re.fullmatch(r'/api/messages/([^/]+)/reply-seed', path)
        if m_seed:
            msg_id = m_seed.group(1)
            to_all = qs.get('to_all', ['false'])[0].lower() in ('1', 'true', 'yes')
            seed = service.get_reply_seed(msg_id, to_all=to_all)
            self.send_json(seed)
            return

        # /api/tags
        if path == '/api/tags':
            tags = service.get_all_tags()
            self.send_json(tags)
            return

        # /api/contacts?q=...
        if path == '/api/contacts':
            q = qs.get('q', [''])[0]
            contacts = service.get_contacts(q)
            self.send_json(contacts)
            return

        # /api/accounts
        if path == '/api/accounts':
            accounts = settings.smtp_accounts if settings.smtp_accounts else ['default']
            self.send_json({'accounts': accounts})
            return

        self.send_error_json("Not found", status=HTTPStatus.NOT_FOUND)

    # -----------------------------------------------------------------------
    # Route Handlers: POST
    # -----------------------------------------------------------------------

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if not self._check_auth():
            return

        # /api/tag
        if path == '/api/tag':
            data = self._read_json()
            ids = data.get('ids', [])
            add_tags = data.get('add', [])
            remove_tags = data.get('remove', [])
            ok = service.modify_tags(ids, add_tags=add_tags, remove_tags=remove_tags)
            self.send_json({'ok': ok})
            return

        # /api/threads/{thread_id}/archive
        m_arch = re.fullmatch(r'/api/threads/([^/]+)/archive', path)
        if m_arch:
            ok = service.archive_thread(m_arch.group(1))
            self.send_json({'ok': ok})
            return

        # /api/threads/{thread_id}/unarchive
        m_unarch = re.fullmatch(r'/api/threads/([^/]+)/unarchive', path)
        if m_unarch:
            ok = service.unarchive_thread(m_unarch.group(1))
            self.send_json({'ok': ok})
            return

        # /api/threads/{thread_id}/trash
        m_trash = re.fullmatch(r'/api/threads/([^/]+)/trash', path)
        if m_trash:
            ok = service.trash_thread(m_trash.group(1))
            self.send_json({'ok': ok})
            return

        # /api/threads/{thread_id}/untrash
        m_untrash = re.fullmatch(r'/api/threads/([^/]+)/untrash', path)
        if m_untrash:
            ok = service.untrash_thread(m_untrash.group(1))
            self.send_json({'ok': ok})
            return

        # /api/threads/{thread_id}/star
        m_star = re.fullmatch(r'/api/threads/([^/]+)/star', path)
        if m_star:
            data = self._read_json()
            flag = bool(data.get('flag', True))
            ok = service.toggle_flag(m_star.group(1), flag)
            self.send_json({'ok': ok})
            return

        # /api/send
        if path == '/api/send':
            self._handle_send()
            return

        self.send_error_json("Not found", status=HTTPStatus.NOT_FOUND)

    def _handle_send(self) -> None:
        """Handle outbound message submission (JSON or multipart)."""
        content_type = self.headers.get('Content-Type', '')

        if 'multipart/form-data' in content_type:
            raw_body = self._read_body()
            # Prefix synthetic RFC headers so email module parses multipart
            hdr = f"Content-Type: {content_type}\r\n\r\n".encode('utf-8')
            msg = email.message_from_bytes(hdr + raw_body, policy=email.policy.default)

            fields: dict[str, str] = {}
            attachments: list[tuple[str, str, bytes]] = []

            for part in msg.iter_parts():
                name = part.get_param('name', header='content-disposition')
                filename = part.get_filename()
                if filename:
                    raw_payload = part.get_payload(decode=True)
                    payload_bytes = raw_payload if isinstance(raw_payload, bytes) else b''
                    c_type = part.get_content_type()
                    attachments.append((str(filename), c_type, payload_bytes))
                elif isinstance(name, str):
                    text = part.get_content()
                    if isinstance(text, str):
                        fields[name] = text

            account = fields.get('account', '')
            to = [x.strip() for x in fields.get('to', '').split(',') if x.strip()]
            cc = [x.strip() for x in fields.get('cc', '').split(',') if x.strip()]
            bcc = [x.strip() for x in fields.get('bcc', '').split(',') if x.strip()]
            subject = fields.get('subject', '')
            body_text = fields.get('body_text', '')
            in_reply_to = fields.get('in_reply_to', '')
            references = fields.get('references', '')

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
                self.send_json({'ok': True, 'message': err_or_msg})
            else:
                self.send_error_json(err_or_msg, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # Fallback to JSON payload
        data = self._read_json()
        account = data.get('account', '')
        to = data.get('to', [])
        if isinstance(to, str):
            to = [x.strip() for x in to.split(',') if x.strip()]
        cc = data.get('cc', [])
        if isinstance(cc, str):
            cc = [x.strip() for x in cc.split(',') if x.strip()]
        bcc = data.get('bcc', [])
        if isinstance(bcc, str):
            bcc = [x.strip() for x in bcc.split(',') if x.strip()]
        subject = data.get('subject', '')
        body_text = data.get('body_text', '')
        in_reply_to = data.get('in_reply_to', '')
        references = data.get('references', '')

        ok, err_or_msg = service.send_email(
            account=account,
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body_text=body_text,
            in_reply_to=in_reply_to,
            references=references,
        )
        if ok:
            self.send_json({'ok': True, 'message': err_or_msg})
        else:
            self.send_error_json(err_or_msg, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_static_file(self, rel_path: str) -> None:
        """Serve a static file from the static/ directory."""
        target = (STATIC_DIR / rel_path).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self.send_error_json("File not found", status=HTTPStatus.NOT_FOUND)
            return

        mime_type, _ = mimetypes.guess_type(str(target))
        if not mime_type:
            if target.suffix == '.js':
                mime_type = 'application/javascript'
            elif target.suffix == '.css':
                mime_type = 'text/css'
            elif target.suffix == '.html':
                mime_type = 'text/html'
            else:
                mime_type = 'application/octet-stream'

        try:
            content = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', f"{mime_type}; charset=utf-8" if 'text' in mime_type or 'javascript' in mime_type else mime_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self.wfile.write(content)
        except OSError as e:
            logger.warning('Failed reading static file %s: %s', target, e)
            self.send_error_json("Could not read file", status=HTTPStatus.INTERNAL_SERVER_ERROR)


class LazarusHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Threaded HTTP server for Lazarus web client."""
    daemon_threads = True
    allow_reuse_address = True


def create_server(host: str | None = None, port: int | None = None) -> LazarusHTTPServer:
    """Instantiate a threaded HTTP server."""
    h = host if host is not None else getattr(settings, 'web_host', '127.0.0.1')
    p = port if port is not None else getattr(settings, 'web_port', 8080)
    return LazarusHTTPServer((h, p), LazarusRequestHandler)


def run_server(host: str | None = None, port: int | None = None) -> None:
    """Run the server until interrupted."""
    server = create_server(host, port)
    sa = server.socket.getsockname()
    print(f"Lazarus mobile web server listening on http://{sa[0]}:{sa[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Lazarus web server...")
    finally:
        server.server_close()
