"""Integration tests for NedMcpServer and JSON-RPC 2.0 stdio handling."""

from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest

from ned_mcp.access import AccountPolicy
from ned_mcp.server import NedMcpServer, parse_args


@pytest.fixture
def mock_client():
    client = MagicMock()
    # Default to 1 message so account validation checks pass
    client.count.return_value = 1
    return client


def test_server_initialize(mock_client):
    """Test MCP initialize handshake."""
    policy = AccountPolicy("work")
    server = NedMcpServer(client=mock_client, policy=policy)

    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {},
    }
    resp = server.handle_request(req)
    assert resp is not None
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"]["name"] == "ned-mcp"
    assert "tools" in resp["result"]["capabilities"]


def test_server_ping_and_notifications(mock_client):
    """Test ping and notification handling."""
    policy = AccountPolicy("work")
    server = NedMcpServer(client=mock_client, policy=policy)

    # Ping
    ping_resp = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert ping_resp == {"jsonrpc": "2.0", "id": 2, "result": {}}

    # Notification (no id) returns None
    notify_resp = server.handle_request({
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    })
    assert notify_resp is None


def test_server_tools_list_permissions(mock_client):
    """Test tools/list reflects permission flags (e.g. send_email inclusion)."""
    # 1. Read/triage only (allow_send=False)
    policy_no_send = AccountPolicy("work", allow_send=False)
    server_no_send = NedMcpServer(client=mock_client, policy=policy_no_send)
    resp = server_no_send.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert resp is not None
    tool_names = [t["name"] for t in resp["result"]["tools"]]
    assert "search_threads" in tool_names
    assert "get_thread" in tool_names
    assert "archive_threads" in tool_names
    assert "trash_threads" in tool_names
    assert "restore_threads" in tool_names
    assert "apply_tags" in tool_names
    assert "list_tags" in tool_names
    assert "send_email" not in tool_names

    # 2. With allow_send=True
    policy_send = AccountPolicy("work", allow_send=True)
    server_send = NedMcpServer(client=mock_client, policy=policy_send)
    resp_send = server_send.handle_request({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    assert resp_send is not None
    tool_names_send = [t["name"] for t in resp_send["result"]["tools"]]
    assert "send_email" in tool_names_send


def test_server_search_threads(mock_client):
    """Test search_threads tool call with account scoping."""
    policy = AccountPolicy("work")
    server = NedMcpServer(client=mock_client, policy=policy)

    mock_client.search.return_value = [
        {
            "thread": "thread-001",
            "subject": "Quarterly Budget",
            "authors": "Accounting",
            "date_relative": "today",
            "tags": ["inbox", "work"],
            "matched": 1,
            "total": 1,
        }
    ]

    call_req = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "search_threads",
            "arguments": {"query": "tag:inbox", "limit": 5},
        },
    }
    resp = server.handle_request(call_req)
    assert resp is not None
    assert resp["result"]["isError"] is False
    mock_client.search.assert_called_with("(path:work/**) and (tag:inbox)", limit=5, offset=0)

    content = json.loads(resp["result"]["content"][0]["text"])
    assert len(content) == 1
    assert content[0]["thread_id"] == "thread-001"
    assert content[0]["subject"] == "Quarterly Budget"


def test_server_get_thread(mock_client):
    """Test get_thread tool call with account boundary verification."""
    policy = AccountPolicy("work")
    server = NedMcpServer(client=mock_client, policy=policy)

    # 1. Success case
    mock_client.count.return_value = 1
    mock_client.get_thread.return_value = {
        "thread_id": "t123",
        "subject": "Discussion",
        "tags": ["inbox"],
        "messages": [
            {
                "id": "msg-1",
                "from": "Lead <lead@test>",
                "to": "Dev <dev@test>",
                "subject": "Discussion",
                "body_text": "Top body\n> Quoted old body",
                "attachments": [],
            }
        ],
    }

    call_req = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "get_thread",
            "arguments": {"thread_id": "thread:t123", "include_quoted": False},
        },
    }
    resp = server.handle_request(call_req)
    assert resp is not None
    assert resp["result"]["isError"] is False
    thread_data = json.loads(resp["result"]["content"][0]["text"])
    assert thread_data["thread_id"] == "t123"
    assert "Top body" in thread_data["messages"][0]["body"]
    assert "[...quoted reply text hidden...]" in thread_data["messages"][0]["body"]

    # 2. Account boundary violation
    mock_client.count.return_value = 0
    call_req_fail = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "get_thread",
            "arguments": {"thread_id": "thread:other_account_thread"},
        },
    }
    resp_fail = server.handle_request(call_req_fail)
    assert resp_fail is not None
    assert resp_fail["result"]["isError"] is True
    assert "does not belong to allowed account" in resp_fail["result"]["content"][0]["text"]


def test_server_archive_threads(mock_client):
    """Test archive_threads tool call."""
    policy = AccountPolicy("work")
    server = NedMcpServer(client=mock_client, policy=policy)

    mock_client.count.return_value = 1
    mock_client.modify_tags.return_value = True
    mock_client.archive_batch_to_local.return_value = True

    # Tag-only archive
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {
            "name": "archive_threads",
            "arguments": {"thread_ids": ["thread:t1", "thread:t2"]},
        },
    })
    assert resp is not None
    assert resp["result"]["isError"] is False
    assert "Archived 2 thread(s)" in resp["result"]["content"][0]["text"]
    mock_client.modify_tags.assert_called_with(threads=["thread:t1", "thread:t2"], remove=["inbox", "unread"])

    # Local maildir archive
    resp_local = server.handle_request({
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {
            "name": "archive_threads",
            "arguments": {"thread_ids": ["thread:t1"], "move_to_local_archive": True},
        },
    })
    assert resp_local is not None
    assert resp_local["result"]["isError"] is False
    assert "Moved 1 thread(s) to local Archive" in resp_local["result"]["content"][0]["text"]
    mock_client.archive_batch_to_local.assert_called_with(threads=["thread:t1"])


def test_server_trash_and_restore(mock_client):
    """Test trash_threads and restore_threads tool calls."""
    policy = AccountPolicy("work")
    server = NedMcpServer(client=mock_client, policy=policy)

    mock_client.count.return_value = 1
    mock_client.trash_batch.return_value = True
    mock_client.restore_batch.return_value = True

    # Trash
    resp_trash = server.handle_request({
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {
            "name": "trash_threads",
            "arguments": {"thread_ids": ["thread:t1"]},
        },
    })
    assert resp_trash is not None
    assert resp_trash["result"]["isError"] is False
    assert "Moved 1 thread(s) to Trash" in resp_trash["result"]["content"][0]["text"]
    mock_client.trash_batch.assert_called_with(threads=["thread:t1"])

    # Restore
    resp_restore = server.handle_request({
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {
            "name": "restore_threads",
            "arguments": {"thread_ids": ["thread:t1"]},
        },
    })
    assert resp_restore is not None
    assert resp_restore["result"]["isError"] is False
    assert "Restored 1 thread(s) from Trash" in resp_restore["result"]["content"][0]["text"]
    mock_client.restore_batch.assert_called_with(threads=["thread:t1"])


def test_server_apply_tags(mock_client):
    """Test apply_tags enforces whitelist."""
    policy = AccountPolicy("work", allowed_tags=["todo", "archive"])
    server = NedMcpServer(client=mock_client, policy=policy)

    mock_client.count.return_value = 1
    mock_client.modify_tags.return_value = True

    # 1. Allowed tag
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 12,
        "method": "tools/call",
        "params": {
            "name": "apply_tags",
            "arguments": {
                "thread_ids": ["thread:t1"],
                "add": ["todo"],
                "remove": ["archive"],
            },
        },
    })
    assert resp is not None
    assert resp["result"]["isError"] is False
    assert "Updated tags (+todo -archive)" in resp["result"]["content"][0]["text"]
    mock_client.modify_tags.assert_called_with(
        threads=["thread:t1"],
        messages=[],
        add=["todo"],
        remove=["archive"],
    )

    # 2. Denied tag
    resp_denied = server.handle_request({
        "jsonrpc": "2.0",
        "id": 13,
        "method": "tools/call",
        "params": {
            "name": "apply_tags",
            "arguments": {
                "thread_ids": ["thread:t1"],
                "add": ["forbidden_tag"],
            },
        },
    })
    assert resp_denied is not None
    assert resp_denied["result"]["isError"] is True
    assert "Tag modification denied" in resp_denied["result"]["content"][0]["text"]


def test_server_list_tags(mock_client):
    """Test list_tags respects tag whitelist filter."""
    mock_client.get_tags.return_value = [
        {"tag": "inbox", "count": 10},
        {"tag": "todo", "count": 5},
        {"tag": "private", "count": 2},
    ]

    # Whitelist filters results
    policy = AccountPolicy("work", allowed_tags=["inbox", "todo"])
    server = NedMcpServer(client=mock_client, policy=policy)

    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 14,
        "method": "tools/call",
        "params": {"name": "list_tags", "arguments": {}},
    })
    assert resp is not None
    assert resp["result"]["isError"] is False
    tags = json.loads(resp["result"]["content"][0]["text"])
    assert len(tags) == 2
    assert [t["tag"] for t in tags] == ["inbox", "todo"]


def test_server_send_email(mock_client):
    """Test send_email builds RFC822 message and sends via account."""
    policy = AccountPolicy("work", allow_send=True)
    server = NedMcpServer(client=mock_client, policy=policy)
    mock_client.send_message.return_value = (True, "Delivered")

    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 15,
        "method": "tools/call",
        "params": {
            "name": "send_email",
            "arguments": {
                "to": ["partner@example.com"],
                "subject": "Status Report",
                "body": "All deliverables on schedule.",
            },
        },
    })
    assert resp is not None
    assert resp["result"]["isError"] is False
    assert "Email sent successfully" in resp["result"]["content"][0]["text"]
    assert mock_client.send_message.call_count == 1
    called_account, called_bytes = mock_client.send_message.call_args[0]
    assert called_account == "work"
    assert b"Subject: Status Report" in called_bytes
    assert b"To: partner@example.com" in called_bytes


def test_parse_args_cli():
    """Test command-line argument parsing for ned-mcp."""
    args = parse_args([
        "--account", "work",
        "--allow-tags", "todo,followup",
        "--allow-send",
        "--socket", "/tmp/ned.sock",
    ])
    assert args.account == ["work"]
    assert args.allow_tags == "todo,followup"
    assert args.allow_send is True
    assert args.socket == "/tmp/ned.sock"
