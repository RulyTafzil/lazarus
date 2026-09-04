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
"""OpenAPI description of the NED HTTP API.

Served live by the daemon at ``GET /api/v1/openapi.json`` so clients can
introspect the exact surface the running binary implements. Kept in one
place (this module) mirroring ``ned/handler.py``; when a route changes,
update both.

Canonical endpoint names (no aliases):

    GET   /api/v1/threads                     search threads (notmuch query)
    GET   /api/v1/threads/{id}                full thread tree
    GET   /api/v1/messages                    search messages
    GET   /api/v1/messages/{id}               one raw notmuch message dict
    GET   /api/v1/messages/{id}/parts/{part_id}   decoded part / attachment
    GET   /api/v1/messages/{id}/reply-seed    reply headers + quoted body + sig
    GET   /api/v1/tags                        tags with thread counts
    GET   /api/v1/contacts                    address autocomplete
    GET   /api/v1/accounts                    accounts + email/PGP identity
    GET   /api/v1/signatures                  per-account signatures (+ HTML)
    GET   /api/v1/count                       thread/message counts
    GET   /api/v1/ping | /api/v1/health       liveness
    GET   /api/v1/events                      SSE invalidation stream
    GET   /api/v1/openapi.json                this document
    POST  /api/v1/tags                        modify tags on queries, threads, or messages
    POST  /api/v1/trash                       move matching files to Trash (batch)
    POST  /api/v1/restore                     restore files from Trash to INBOX (batch)
    POST  /api/v1/move-archive                move files to local Archive (batch)
    POST  /api/v1/threads/{id}/trash          move thread to account Trash
    POST  /api/v1/threads/{id}/restore        restore thread from Trash to INBOX
    POST  /api/v1/threads/{id}/move-archive   move thread to local Archive
    POST  /api/v1/messages/{id}/trash         move message to account Trash
    POST  /api/v1/messages/{id}/restore       restore message from Trash to INBOX
    POST  /api/v1/messages/{id}/move-archive  move message to local Archive
    POST  /api/v1/threads/{id}/star           set/clear flagged
    POST  /api/v1/expunge                     Maildir T flag on tag:trash
    POST  /api/v1/rules                       apply filter rules
    POST  /api/v1/index                       notmuch new (no hooks)
    POST  /api/v1/sync                        mbsync + notmuch new + rules
    POST  /api/v1/send                        field/multipart OR raw MIME
"""

from typing import Any

_TITLE = "NED — Notmuch Email Daemon"
_VERSION = "0.3"


def _raw_notmuch(notes: str) -> dict[str, Any]:
    """A response schema note describing notmuch-JSON passthrough."""
    return {
        "type": "array" if "list" in notes else "object",
        "description": notes,
    }


def build_spec() -> dict[str, Any]:
    """Return the OpenAPI 3.0 JSON document for the NED API."""
    return {
        "openapi": "3.0.3",
        "info": {
            "title": _TITLE,
            "version": _VERSION,
            "description": (
                "Headless notmuch email daemon. Thread/message payloads are raw "
                "`notmuch search/show --format=json` dicts — see notmuch(1) for "
                "the message schema. The mobile PWA is served at /."
            ),
        },
        "servers": [
            {"url": "unix:///run/user/$UID/ned/ned.sock", "description": "Local Unix domain socket (no auth; OS permissions)"},
            {"url": "http://<tailscale-ip>:8080", "description": "Tailscale TCP (Bearer token optional; WireGuard provides transport security)"},
        ],
        "security": [{"bearerAuth": []}, {}],
        "paths": {
            "/api/v1/threads": {
                "get": {
                    "summary": "Search threads",
                    "description": "Full notmuch search over the daemon query.",
                    "parameters": [
                        {"name": "q", "in": "query", "required": False,
                         "description": "notmuch query (default tag:inbox)", "schema": {"type": "string"}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 50}},
                        {"name": "offset", "in": "query", "schema": {"type": "integer", "default": 0}},
                    ],
                    "responses": {"200": {"description": "List of notmuch search thread dicts", "content": {"application/json": {"schema": {"type": "array", "items": {"type": "object"}}}}}},
                },
            },
            "/api/v1/threads/{id}": {
                "get": {
                    "summary": "Fetch one thread tree",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "Thread id (URL-encoded)"}],
                    "responses": {"200": {"description": "Thread tree (notmuch show JSON) with messages", "content": {"application/json": {"schema": _raw_notmuch("thread tree")}}}},
                },
            },
            "/api/v1/messages": {
                "get": {
                    "summary": "Search messages matching a query",
                    "parameters": [
                        {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 1000}},
                        {"name": "offset", "in": "query", "schema": {"type": "integer", "default": 0}},
                    ],
                    "responses": {"200": {"description": "List of message ids", "content": {"application/json": {"schema": {"type": "array", "items": {"type": "string"}}}}}},
                },
            },
            "/api/v1/messages/{id}": {
                "get": {
                    "summary": "Fetch one raw message dict (cheap view refresh)",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "RFC message-id (URL-encoded)"}],
                    "responses": {"200": {"description": "notmuch show message dict", "content": {"application/json": {"schema": _raw_notmuch("message")}}}},
                },
            },
            "/api/v1/messages/{id}/parts/{part_id}": {
                "get": {
                    "summary": "Download a decoded body part or binary attachment",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "part_id", "in": "path", "required": True, "schema": {"type": "integer"}},
                    ],
                    "responses": {
                        "200": {"description": "Raw part bytes (Content-Type from part; attachment has Content-Disposition)"},
                        "404": {"description": "Part not found"},
                    },
                },
            },
            "/api/v1/messages/{id}/reply-seed": {
                "get": {
                    "summary": "Generate reply data (headers, quoted body, signature)",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "to_all", "in": "query", "schema": {"type": "boolean", "default": False}},
                    ],
                    "responses": {"200": {"description": "Reply seed {to, cc, subject, body, account, signatures?}", "content": {"application/json": {"schema": {"type": "object"}}}}},
                },
            },
            "/api/v1/messages/{id}/tags": {
                "post": {
                    "summary": "Modify tags on a single message",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "RFC message-id, URL-encoded"}],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {"add": {"type": "array", "items": {"type": "string"}}, "remove": {"type": "array", "items": {"type": "string"}}}}}}},
                    "responses": {"200": {"description": "{status: ok, ok: true}", "content": {"application/json": {"schema": {"type": "object"}}}}},
                },
            },
            "/api/v1/tags": {
                "get": {
                    "summary": "List all known tags with thread counts",
                    "responses": {"200": {"description": "List of {name, count}", "content": {"application/json": {"schema": {"type": "array", "items": {"type": "object"}}}}}},
                },
                "post": {
                    "summary": "Modify tags on queries, threads, or messages",
                    "description": "Body: {\"queries\": [...], \"threads\": [...], \"messages\": [...], \"add\": [...], \"remove\": [...]}. Legacy ids or query accepted.",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "string"}}, "threads": {"type": "array", "items": {"type": "string"}}, "messages": {"type": "array", "items": {"type": "string"}}, "add": {"type": "array", "items": {"type": "string"}}, "remove": {"type": "array", "items": {"type": "string"}}}}}}},
                    "responses": {"200": {"description": "{status: ok, ok: true}", "content": {"application/json": {"schema": {"type": "object"}}}}},
                },
            },
            "/api/v1/contacts": {
                "get": {
                    "summary": "Address autocomplete",
                    "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "List of matching address dicts", "content": {"application/json": {"schema": {"type": "array", "items": {"type": "object"}}}}}},
                },
            },
            "/api/v1/accounts": {
                "get": {
                    "summary": "Sender accounts + per-account mail identity",
                    "responses": {"200": {"description": "{accounts: [...], email: {acct: addr}, gnupg_keyid: {acct: key|null}}", "content": {"application/json": {"schema": {"type": "object"}}}}},
                },
            },
            "/api/v1/signatures": {
                "get": {
                    "summary": "Per-account signatures",
                    "responses": {"200": {"description": "{use_signature: bool, signatures: {acct: text}, signatures_html: {acct: html}}", "content": {"application/json": {"schema": {"type": "object"}}}}},
                },
            },
            "/api/v1/count": {
                "get": {"summary": "Count threads matching a query", "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}, {"name": "output", "in": "query", "schema": {"type": "string", "default": "threads"}}], "responses": {"200": {"description": "JSON number", "content": {"application/json": {"schema": {"type": "integer"}}}}}},
                "post": {"summary": "Batch thread counts for several queries", "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "string"}}}}}}}, "responses": {"200": {"description": "JSON array of counts", "content": {"application/json": {"schema": {"type": "array", "items": {"type": "integer"}}}}}}},
            },
            "/api/v1/ping": {
                "get": {"summary": "Liveness probe (also /api/v1/health)", "responses": {"200": {"description": "Text pong"}}},
            },
            "/api/v1/events": {
                "get": {
                    "summary": "Server-Sent Events invalidation stream",
                    "description": "event: invalidate — data: {\"scope\": \"threads\"|\"thread\", \"id\": ?, \"reason\": \"sync|tag|send|rules|...\"}",
                    "responses": {"200": {"description": "text/event-stream"}},
                },
            },
            "/api/v1/openapi.json": {
                "get": {"summary": "This OpenAPI description", "responses": {"200": {"description": "OpenAPI 3.0 JSON"}}},
            },
            "/api/v1/trash": {"post": {"summary": "Batch move files matching queries, threads, or messages to Trash", "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "string"}}, "threads": {"type": "array", "items": {"type": "string"}}, "messages": {"type": "array", "items": {"type": "string"}}, "unmark": {"type": "boolean", "default": False}}}}}}, "responses": {"200": {"description": "{status: ok, ok: true}"}}}},
            "/api/v1/restore": {"post": {"summary": "Batch restore files from Trash to INBOX", "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "string"}}, "threads": {"type": "array", "items": {"type": "string"}}, "messages": {"type": "array", "items": {"type": "string"}}, "unmark": {"type": "boolean", "default": False}}}}}}, "responses": {"200": {"description": "{status: ok, ok: true}"}}}},
            "/api/v1/move-archive": {"post": {"summary": "Batch move files matching queries, threads, or messages to local Archive", "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "string"}}, "threads": {"type": "array", "items": {"type": "string"}}, "messages": {"type": "array", "items": {"type": "string"}}, "unmark": {"type": "boolean", "default": False}}}}}}, "responses": {"200": {"description": "{status: ok, ok: true}"}}}},
            "/api/v1/threads/{id}/archive": {"post": {"summary": "Archive thread: -inbox -unread and move to Archive", "responses": {"200": {"description": "{status: ok}"}}}},
            "/api/v1/threads/{id}/trash": {"post": {"summary": "Trash thread (+trash -inbox -unread + move to Trash)", "responses": {"200": {"description": "{status: ok}"}}}},
            "/api/v1/threads/{id}/restore": {"post": {"summary": "Restore thread from Trash to INBOX", "responses": {"200": {"description": "{status: ok}"}}}},
            "/api/v1/threads/{id}/move-archive": {"post": {"summary": "Move thread files to local Archive", "responses": {"200": {"description": "{status: ok}"}}}},
            "/api/v1/threads/{id}/unarchive": {"post": {"summary": "Restore archived thread to inbox", "responses": {"200": {"description": "{status: ok}"}}}},
            "/api/v1/threads/{id}/untrash": {"post": {"summary": "Restore trashed thread from Trash to INBOX", "responses": {"200": {"description": "{status: ok}"}}}},
            "/api/v1/threads/{id}/star": {
                "post": {
                    "summary": "Set/clear the flagged tag",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {"flag": {"type": "boolean", "default": True}}}}}},
                    "responses": {"200": {"description": "{status: ok, starred: bool}", "content": {"application/json": {"schema": {"type": "object"}}}}},
                },
            },
            "/api/v1/threads/{id}/tags": {
                "post": {
                    "summary": "Modify tags on a single thread",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "Thread ID, URL-encoded"}],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {"add": {"type": "array", "items": {"type": "string"}}, "remove": {"type": "array", "items": {"type": "string"}}}}}}},
                    "responses": {"200": {"description": "{status: ok, ok: true}", "content": {"application/json": {"schema": {"type": "object"}}}}},
                },
            },
            "/api/v1/messages/{id}/trash": {"post": {"summary": "Move message files to Trash", "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "RFC message ID"}, {"name": "thread_id", "in": "query", "schema": {"type": "string"}}], "responses": {"200": {"description": "{status: ok, trashed: id, ok: true}"}}}},
            "/api/v1/messages/{id}/restore": {"post": {"summary": "Restore message files from Trash to INBOX", "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "RFC message ID"}, {"name": "thread_id", "in": "query", "schema": {"type": "string"}}], "responses": {"200": {"description": "{status: ok, restored: id, ok: true}"}}}},
            "/api/v1/messages/{id}/move-archive": {"post": {"summary": "Move message files to local Archive", "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "RFC message ID"}, {"name": "thread_id", "in": "query", "schema": {"type": "string"}}], "responses": {"200": {"description": "{status: ok, archived: id, ok: true}"}}}},
            "/api/v1/threads/archive": {"post": {"summary": "Batch archive several queries", "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "string"}}}}}}}, "responses": {"200": {"description": "{status: ok}"}}}},
            "/api/v1/threads/trash": {"post": {"summary": "Batch trash several queries", "responses": {"200": {"description": "{status: ok}"}}}},
            "/api/v1/threads/unarchive": {"post": {"summary": "Batch unarchive several queries", "responses": {"200": {"description": "{status: ok}"}}}},
            "/api/v1/threads/untrash": {"post": {"summary": "Batch untrash several queries", "responses": {"200": {"description": "{status: ok}"}}}},
            "/api/v1/expunge": {"post": {"summary": "Maildir T flag on every tag:trash file (irreversible)", "responses": {"200": {"description": "{status: ok}"}}}},
            "/api/v1/rules": {"post": {"summary": "Apply configured filter rules", "responses": {"200": {"description": "{status: ok, matched: int}"}}}},
            "/api/v1/index": {"post": {"summary": "Run notmuch new --no-hooks", "responses": {"200": {"description": "{status: ok}"}}}},
            "/api/v1/sync": {"post": {"summary": "mbsync per account + notmuch new + filter rules", "responses": {"200": {"description": "{status: ok, message: summary}"}}}},
            "/api/v1/send": {
                "post": {
                    "summary": "Send mail — field mode or raw MIME mode",
                    "description": "Field mode: JSON {account, to, cc, bcc, subject, body_text, in_reply_to, references} or multipart/form-data with attachments. Raw mode: {\"account\": ..., \"message_b64\": base64(RFC822 message)} — client-built MIME (HTML, inline images, PGP).",
                    "responses": {"200": {"description": "{status: ok, message}", }, "default": {"description": "{error: message}"}},
                },
            },
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer",
                               "description": "Required only over TCP when settings.web_token is set; Unix socket uses OS file permissions. Token may also be passed as ?token= query param (used by the SSE EventSource)."}
            },
            "schemas": {
                "Error": {"type": "object", "properties": {"error": {"type": "string"}}},
                "NotmuchMessage": {"type": "object", "description": "Raw notmuch show --format=json message dict; see notmuch(1)"},
            },
        },
    }