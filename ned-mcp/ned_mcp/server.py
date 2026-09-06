"""Model Context Protocol (MCP) server for NED.

Exposes account-scoped, non-destructive email search, inspection, and triage
tools to AI agents over standard input/output using JSON-RPC 2.0.
"""

from __future__ import annotations

import argparse
from email.message import EmailMessage
import json
import logging
import sys
from typing import Any, Callable, Optional

from .access import AccessDeniedError, AccountPolicy
from .client import NedClient, NedError, NedNotFoundError
from .formatters import (
    format_search_results,
    sanitize_thread,
)

logger = logging.getLogger("ned_mcp")


class NedMcpServer:
    """JSON-RPC 2.0 MCP server wrapping NedClient and AccountPolicy."""

    PROTOCOL_VERSION = "2024-11-05"
    SERVER_NAME = "ned-mcp"
    SERVER_VERSION = "0.3"

    def __init__(
        self,
        client: NedClient,
        policy: AccountPolicy,
    ) -> None:
        self.client = client
        self.policy = policy
        self._dispatch_map: dict[str, Callable[[dict[str, Any]], Any]] = {
            "search_threads": self._tool_search_threads,
            "get_thread": self._tool_get_thread,
            "list_tags": self._tool_list_tags,
        }
        if self.policy.can_mutate_tags:
            self._dispatch_map["apply_tags"] = self._tool_apply_tags
        if self.policy.allow_trash:
            self._dispatch_map["trash_threads"] = self._tool_trash_threads
            self._dispatch_map["restore_threads"] = self._tool_restore_threads
        if self.policy.allow_archive:
            self._dispatch_map["archive_threads"] = self._tool_archive_threads
        if self.policy.allow_send:
            self._dispatch_map["send_email"] = self._tool_send_email

    # -----------------------------------------------------------------------
    # Tool Definitions
    # -----------------------------------------------------------------------

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return MCP tool schemas for registered capabilities."""
        tools = [
            {
                "name": "search_threads",
                "description": (
                    f"Search email threads within the '{self.policy.primary_account}' account. "
                    "Returns compact metadata summaries."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Notmuch search query expression (e.g. 'tag:inbox and not tag:unread').",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum threads to return (default: 10).",
                            "default": 10,
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Pagination offset (default: 0).",
                            "default": 0,
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "get_thread",
                "description": (
                    "Fetch messages in an email thread with quote collapsing and attachment stubs. "
                    "Verifies thread belongs to the allowed account."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "thread_id": {
                            "type": "string",
                            "description": "Thread identifier (e.g. 'thread:0000000000012345').",
                        },
                        "max_messages": {
                            "type": "integer",
                            "description": "Maximum newest messages to return (default: 10).",
                            "default": 10,
                        },
                        "include_quoted": {
                            "type": "boolean",
                            "description": "Whether to include older quoted reply text (default: false).",
                            "default": False,
                        },
                    },
                    "required": ["thread_id"],
                },
            },
        ]

        if self.policy.allow_archive:
            tools.append({
                "name": "archive_threads",
                "description": (
                    "Archive one or more email threads. By default removes 'inbox' and 'unread' tags. "
                    "Optionally moves files to the local Archive Maildir folder."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "thread_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of thread IDs to archive.",
                        },
                        "move_to_local_archive": {
                            "type": "boolean",
                            "description": "Move files to local archive Maildir instead of tag-only archive (default: false).",
                            "default": False,
                        },
                    },
                    "required": ["thread_ids"],
                },
            })

        if self.policy.allow_trash:
            tools.extend([
                {
                    "name": "trash_threads",
                    "description": (
                        "Move email threads to the account Trash Maildir folder and apply '+trash -inbox -unread'. "
                        "Non-destructive and reversible."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "thread_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of thread IDs to move to trash.",
                            },
                        },
                        "required": ["thread_ids"],
                    },
                },
                {
                    "name": "restore_threads",
                    "description": (
                        "Restore email threads from Trash back to the account INBOX Maildir folder and tag '+inbox -trash'."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "thread_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of thread IDs to restore.",
                            },
                        },
                        "required": ["thread_ids"],
                    },
                },
            ])

        if self.policy.can_mutate_tags:
            tools.append({
                "name": "apply_tags",
                "description": (
                    "Add or remove tags on specified threads or messages. "
                    "Enforces the account tag whitelist."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "thread_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Thread IDs to modify.",
                        },
                        "message_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Message IDs to modify.",
                        },
                        "add": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of tags to add.",
                        },
                        "remove": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of tags to remove.",
                        },
                    },
                },
            })

        tools.append({
            "name": "list_tags",
            "description": (
                "List available tags and thread counts. "
                "Filtered to permitted tags if a whitelist is configured."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        })

        if self.policy.allow_send:
            tools.append({
                "name": "send_email",
                "description": (
                    f"Send an outbound email via the '{self.policy.primary_account}' SMTP configuration."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "to": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Recipient email addresses.",
                        },
                        "subject": {
                            "type": "string",
                            "description": "Email subject line.",
                        },
                        "body": {
                            "type": "string",
                            "description": "Plaintext message body.",
                        },
                        "cc": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional carbon copy addresses.",
                        },
                        "in_reply_to": {
                            "type": "string",
                            "description": "Optional Message-ID this message replies to.",
                        },
                        "references": {
                            "type": "string",
                            "description": "Optional Message-ID references chain.",
                        },
                    },
                    "required": ["to", "subject", "body"],
                },
            })

        return tools

    # -----------------------------------------------------------------------
    # Tool Handlers
    # -----------------------------------------------------------------------

    def _tool_search_threads(self, args: dict[str, Any]) -> Any:
        query = args.get("query", "").strip()
        limit = int(args.get("limit", 10))
        offset = int(args.get("offset", 0))

        scoped = self.policy.scoped_query(query)
        raw_threads = self.client.search(scoped, limit=limit, offset=offset)
        return format_search_results(raw_threads)

    def _tool_get_thread(self, args: dict[str, Any]) -> Any:
        thread_id = args.get("thread_id", "").strip()
        if not thread_id:
            raise ValueError("thread_id is required.")

        self.policy.validate_thread_in_account(self.client, thread_id)
        raw_thread = self.client.get_thread(thread_id)
        include_quoted = bool(args.get("include_quoted", False))
        max_messages = args.get("max_messages")
        max_m = int(max_messages) if max_messages is not None else 10

        return sanitize_thread(raw_thread, include_quoted=include_quoted, max_messages=max_m)

    def _tool_archive_threads(self, args: dict[str, Any]) -> Any:
        local = bool(args.get("move_to_local_archive", False))
        self.policy.validate_archive(local=local)
        thread_ids = [str(tid).strip() for tid in args.get("thread_ids", []) if str(tid).strip()]
        if not thread_ids:
            raise ValueError("At least one thread_id is required.")

        for tid in thread_ids:
            self.policy.validate_thread_in_account(self.client, tid)

        if local:
            ok = self.client.archive_batch_to_local(threads=thread_ids)
            if ok:
                return f"Moved {len(thread_ids)} thread(s) to local Archive."
            raise NedError("Failed to move threads to local Archive.")
        else:
            ok = self.client.modify_tags(threads=thread_ids, remove=["inbox", "unread"])
            if ok:
                return f"Archived {len(thread_ids)} thread(s) by removing 'inbox' and 'unread' tags."
            raise NedError("Failed to archive threads.")

    def _tool_trash_threads(self, args: dict[str, Any]) -> Any:
        self.policy.validate_trash()
        thread_ids = [str(tid).strip() for tid in args.get("thread_ids", []) if str(tid).strip()]
        if not thread_ids:
            raise ValueError("At least one thread_id is required.")

        for tid in thread_ids:
            self.policy.validate_thread_in_account(self.client, tid)

        ok = self.client.trash_batch(threads=thread_ids)
        if ok:
            return f"Moved {len(thread_ids)} thread(s) to Trash."
        raise NedError("Failed to move threads to Trash.")

    def _tool_restore_threads(self, args: dict[str, Any]) -> Any:
        self.policy.validate_trash()
        thread_ids = [str(tid).strip() for tid in args.get("thread_ids", []) if str(tid).strip()]
        if not thread_ids:
            raise ValueError("At least one thread_id is required.")

        for tid in thread_ids:
            self.policy.validate_thread_in_account(self.client, tid)

        ok = self.client.restore_batch(threads=thread_ids)
        if ok:
            return f"Restored {len(thread_ids)} thread(s) from Trash."
        raise NedError("Failed to restore threads from Trash.")

    def _tool_apply_tags(self, args: dict[str, Any]) -> Any:
        add_tags = args.get("add") or []
        remove_tags = args.get("remove") or []
        thread_ids = [str(tid).strip() for tid in (args.get("thread_ids") or []) if str(tid).strip()]
        message_ids = [str(mid).strip() for mid in (args.get("message_ids") or []) if str(mid).strip()]

        if not thread_ids and not message_ids:
            raise ValueError("At least one thread_id or message_id is required.")
        if not add_tags and not remove_tags:
            raise ValueError("At least one tag to add or remove is required.")

        valid_add, valid_remove = self.policy.validate_tag_mutation(add=add_tags, remove=remove_tags)

        for tid in thread_ids:
            self.policy.validate_thread_in_account(self.client, tid)
        for mid in message_ids:
            self.policy.validate_message_in_account(self.client, mid)

        ok = self.client.modify_tags(
            threads=thread_ids,
            messages=message_ids,
            add=valid_add,
            remove=valid_remove,
        )
        if ok:
            changes = []
            if valid_add:
                changes.append("+" + ", +".join(valid_add))
            if valid_remove:
                changes.append("-" + ", -".join(valid_remove))
            count = len(thread_ids) + len(message_ids)
            return f"Updated tags ({' '.join(changes)}) on {count} target(s)."
        raise NedError("Failed to update tags.")

    def _tool_list_tags(self, args: dict[str, Any]) -> Any:
        tags = self.client.get_tags()
        if self.policy.allowed_tags is not None:
            allowed = self.policy.allowed_tags
            tags = [t for t in tags if t.get("tag") in allowed]
        return tags

    def _tool_send_email(self, args: dict[str, Any]) -> Any:
        account = self.policy.validate_send()

        to_addrs = args.get("to") or []
        if isinstance(to_addrs, str):
            to_addrs = [to_addrs]
        subject = args.get("subject", "").strip()
        body = args.get("body", "").strip()
        cc_addrs = args.get("cc") or []
        if isinstance(cc_addrs, str):
            cc_addrs = [cc_addrs]
        in_reply_to = args.get("in_reply_to")
        references = args.get("references")

        if not to_addrs:
            raise ValueError("At least one recipient in 'to' is required.")
        if not subject:
            raise ValueError("'subject' is required.")
        if not body:
            raise ValueError("'body' is required.")

        msg = EmailMessage()
        msg["To"] = ", ".join(to_addrs)
        msg["Subject"] = subject
        if cc_addrs:
            msg["Cc"] = ", ".join(cc_addrs)
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references

        msg.set_content(body)
        raw_bytes = msg.as_bytes()

        ok, err_or_msg = self.client.send_message(account, raw_bytes)
        if ok:
            return f"Email sent successfully via account '{account}'."
        raise NedError(f"Failed to send email: {err_or_msg}")

    # -----------------------------------------------------------------------
    # JSON-RPC 2.0 Dispatcher
    # -----------------------------------------------------------------------

    def handle_request(self, req: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Handle a single JSON-RPC 2.0 request or notification."""
        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}

        # Notifications (no id)
        if req_id is None:
            if method == "notifications/initialized":
                logger.debug("Client sent notifications/initialized.")
            return None

        # Ping
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        # Initialize
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {},
                    },
                    "serverInfo": {
                        "name": self.SERVER_NAME,
                        "version": self.SERVER_VERSION,
                    },
                },
            }

        # Tools List
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": self.get_tool_definitions(),
                },
            }

        # Tools Call
        if method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}

            handler = self._dispatch_map.get(tool_name)
            if not handler:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method or tool not found: {tool_name}",
                    },
                }

            try:
                result_data = handler(tool_args)
                text_content = (
                    json.dumps(result_data, indent=2)
                    if not isinstance(result_data, str)
                    else result_data
                )
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": text_content,
                            }
                        ],
                        "isError": False,
                    },
                }
            except (AccessDeniedError, ValueError, NedNotFoundError, NedError) as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Error: {exc}",
                            }
                        ],
                        "isError": True,
                    },
                }
            except Exception as exc:
                logger.exception("Unexpected error executing tool %s", tool_name)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Internal server error: {exc}",
                            }
                        ],
                        "isError": True,
                    },
                }

        # Unknown method
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}",
            },
        }

    def run_stdio(self) -> None:
        """Run JSON-RPC server loop over stdin and stdout."""
        logger.info("Starting ned-mcp stdio server for account '%s'", self.policy.primary_account)
        for line in sys.stdin:
            raw_line = line.strip()
            if not raw_line:
                continue
            try:
                req = json.loads(raw_line)
            except Exception as err:
                resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {err}"},
                }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
                continue

            resp = self.handle_request(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()


def parse_args(args: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments for ned-mcp."""
    parser = argparse.ArgumentParser(
        description="Model Context Protocol (MCP) server for NED",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--account",
        "-a",
        action="append",
        required=True,
        help="Account name(s) to scope access to. Can be repeated or comma-separated (e.g. --account work,personal)",
    )
    parser.add_argument(
        "--tags",
        help="Allowed tags to add or remove (e.g. 'todo,followup' or '*' for all tags). Default: no tag mutations allowed",
    )
    parser.add_argument(
        "--allow-tags",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--full-tags",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--allow-trash",
        action="store_true",
        help="Enable trash and restore tools for non-destructive removal",
    )
    parser.add_argument(
        "--allow-archive",
        action="store_true",
        help="Enable archive tool for tag-based archiving (-inbox -unread)",
    )
    parser.add_argument(
        "--allow-archive-to-local",
        action="store_true",
        help="Enable archive tool with disk relocation to local Archive Maildir",
    )
    parser.add_argument(
        "--allow-send",
        action="store_true",
        help="Enable outbound email sending through the scoped account SMTP credentials",
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
    return parser.parse_args(args)


def main(args: Optional[list[str]] = None) -> int:
    """Entry point for the ned-mcp executable."""
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    parsed = parse_args(args)

    tags_arg = parsed.tags or parsed.allow_tags
    full_tags = bool(parsed.full_tags or (tags_arg == "*"))
    allowed_tags = None if full_tags else tags_arg

    policy = AccountPolicy(
        accounts=parsed.account,
        allowed_tags=allowed_tags,
        full_tags=full_tags,
        allow_trash=parsed.allow_trash,
        allow_archive=parsed.allow_archive or parsed.allow_archive_to_local,
        allow_archive_to_local=parsed.allow_archive_to_local,
        allow_send=parsed.allow_send,
    )

    client = NedClient(
        socket_path=parsed.socket,
        base_url=parsed.url,
        token=parsed.token,
    )

    server = NedMcpServer(client=client, policy=policy)
    server.run_stdio()
    return 0
