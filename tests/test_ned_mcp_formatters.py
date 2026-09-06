"""Unit tests for payload sanitization and token formatters in ned-mcp."""

from __future__ import annotations

from ned_mcp.formatters import (
    collapse_quotes,
    extract_attachment_stubs,
    format_search_results,
    sanitize_html_to_plain,
    sanitize_message,
    sanitize_thread,
)


def test_collapse_quotes():
    """Test collapsing older quote reply chains."""
    text = (
        "Hello Alice,\n\n"
        "Sounds like a plan!\n\n"
        "On Monday, Sep 1, 2026, Alice wrote:\n"
        "> Let's start the project.\n"
        "> > Earlier discussion"
    )
    collapsed = collapse_quotes(text)
    assert "Sounds like a plan!" in collapsed
    assert "[...quoted reply text hidden...]" in collapsed
    assert "Let's start the project." not in collapsed
    assert "Earlier discussion" not in collapsed


def test_sanitize_html_to_plain():
    """Test converting raw HTML into clean readable text."""
    html = """
    <div>
        <p>Hello <b>World</b>!</p>
        <p>This is a <a href="http://example.com">link</a> &amp; test.</p>
        <script>alert('evil');</script>
    </div>
    """
    plain = sanitize_html_to_plain(html)
    assert "Hello World!" in plain
    assert "This is a link & test." in plain
    assert "alert" not in plain
    assert "<script>" not in plain


def test_extract_attachment_stubs():
    """Test extracting lightweight metadata stubs."""
    msg = {
        "attachments": [
            {
                "part_id": 2,
                "filename": "proposal.pdf",
                "content_type": "application/pdf",
                "size": 20480,
            }
        ]
    }
    stubs = extract_attachment_stubs(msg)
    assert len(stubs) == 1
    assert stubs[0]["filename"] == "proposal.pdf"
    assert stubs[0]["content_type"] == "application/pdf"
    assert stubs[0]["size_bytes"] == 20480


def test_sanitize_message():
    """Test pruning a raw message dict."""
    msg = {
        "id": "msg-123@domain",
        "headers": {
            "From": "Alice <alice@test>",
            "To": "Bob <bob@test>",
            "Cc": "Carol <carol@test>",
            "Subject": "Sprint Update",
            "Date": "2026-09-06",
            "X-Spam-Status": "No",
            "Received": "by mx.example.com",
        },
        "tags": ["inbox", "unread"],
        "body_text": "Here is the update.\n\n> Quoted text",
        "attachments": [
            {"filename": "spec.txt", "content_type": "text/plain", "size": 100}
        ],
    }

    # Collapsed quotes by default
    pruned = sanitize_message(msg, include_quoted=False)
    assert pruned["id"] == "msg-123@domain"
    assert pruned["from"] == "Alice <alice@test>"
    assert pruned["to"] == "Bob <bob@test>"
    assert pruned["cc"] == "Carol <carol@test>"
    assert pruned["subject"] == "Sprint Update"
    assert pruned["tags"] == ["inbox", "unread"]
    assert "Here is the update." in pruned["body"]
    assert "[...quoted reply text hidden...]" in pruned["body"]
    assert "X-Spam-Status" not in pruned
    assert "Received" not in pruned
    assert len(pruned["attachments"]) == 1
    assert pruned["attachments"][0]["filename"] == "spec.txt"

    # With quoted replies included
    full = sanitize_message(msg, include_quoted=True)
    assert "> Quoted text" in full["body"]


def test_sanitize_thread():
    """Test pruning a full thread with message limits."""
    thread = {
        "thread_id": "thread-456",
        "subject": "Topic",
        "tags": ["work"],
        "messages": [
            {
                "id": f"msg-{i}",
                "from": "user@test",
                "to": "other@test",
                "subject": "Topic",
                "body_text": f"Message body {i}",
            }
            for i in range(1, 6)
        ],
    }

    # Limit to last 2 messages
    sanitized = sanitize_thread(thread, max_messages=2)
    assert sanitized["thread_id"] == "thread-456"
    assert sanitized["message_count"] == 2
    assert len(sanitized["messages"]) == 2
    assert sanitized["messages"][0]["id"] == "msg-4"
    assert sanitized["messages"][1]["id"] == "msg-5"


def test_format_search_results():
    """Test formatting search results."""
    raw = [
        {
            "thread": "thread-001",
            "subject": "Hello",
            "authors": "Alice, Bob",
            "date_relative": "yesterday",
            "tags": ["inbox"],
            "matched": 1,
            "total": 2,
        }
    ]
    formatted = format_search_results(raw)
    assert len(formatted) == 1
    assert formatted[0]["thread_id"] == "thread-001"
    assert formatted[0]["authors"] == "Alice, Bob"
    assert formatted[0]["subject"] == "Hello"
