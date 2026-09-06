"""Token economy and payload pruning for ned-mcp.

Sanitizes email threads and messages before returning them to language models,
minimizing context window consumption and stripping binary attachments.
"""

from __future__ import annotations

import html as html_module
from html.parser import HTMLParser
from typing import Any, Optional


def collapse_quotes(text: str) -> str:
    """Collapse older quoted replies in email body text."""
    if not text:
        return ""

    lines = text.splitlines()
    trimmed: list[str] = []
    in_quote = False

    for line in lines:
        is_quote = line.startswith(">") or (
            line.startswith("On ") and line.rstrip().endswith("wrote:")
        )
        if is_quote:
            if not in_quote:
                trimmed.append("[...quoted reply text hidden...]")
                in_quote = True
        else:
            in_quote = False
            trimmed.append(line)

    return "\n".join(trimmed).strip()


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.result: list[str] = []
        self._ignore = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style", "head", "title"):
            self._ignore = True
        elif tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "li", "hr", "blockquote"):
            self.result.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head", "title"):
            self._ignore = False
        elif tag in ("p", "div", "tr", "li", "blockquote"):
            self.result.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignore:
            self.result.append(data)


def sanitize_html_to_plain(html_content: str) -> str:
    """Convert HTML content into clean plaintext without extra dependencies."""
    if not html_content:
        return ""
    parser = _HTMLTextExtractor()
    parser.feed(html_content)
    raw = "".join(parser.result)
    raw = html_module.unescape(raw)
    lines = [line.strip() for line in raw.splitlines()]
    output: list[str] = []
    prev_blank = False
    for line in lines:
        if not line:
            if not prev_blank:
                output.append("")
                prev_blank = True
        else:
            output.append(line)
            prev_blank = False
    return "\n".join(output).strip()



def extract_attachment_stubs(message_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract lightweight metadata stubs for attachments without raw payload bytes."""
    stubs: list[dict[str, Any]] = []

    raw_attachments = message_data.get("attachments") or []
    for att in raw_attachments:
        if isinstance(att, dict):
            stubs.append({
                "filename": att.get("filename") or f"part-{att.get('part_id', 0)}",
                "content_type": att.get("content_type") or "application/octet-stream",
                "size_bytes": att.get("size") or att.get("content-length") or 0,
            })

    return stubs


def sanitize_message(
    msg: dict[str, Any],
    include_quoted: bool = False,
) -> dict[str, Any]:
    """Prune a single message to essential headers, body, and attachment stubs."""
    headers = msg.get("headers", {})

    msg_id = msg.get("id") or headers.get("Message-ID") or ""
    date_str = msg.get("date") or headers.get("Date") or ""
    subject = msg.get("subject") or headers.get("Subject") or ""
    from_addr = msg.get("from") or headers.get("From") or ""
    to_addr = msg.get("to") or headers.get("To") or ""
    cc_addr = msg.get("cc") or headers.get("Cc") or ""
    tags = msg.get("tags") or []

    # Body extraction
    body_text = (msg.get("body_text") or "").strip()
    if not body_text and msg.get("body_html"):
        body_text = sanitize_html_to_plain(msg["body_html"]).strip()

    if not body_text and "body" in msg:
        body_node = msg["body"]
        if isinstance(body_node, list) and body_node and isinstance(body_node[0], dict):
            content = body_node[0].get("content", "")
            if isinstance(content, str):
                body_text = content.strip()

    if not include_quoted and body_text:
        body_text = collapse_quotes(body_text)

    sanitized: dict[str, Any] = {
        "id": msg_id,
        "from": from_addr,
        "to": to_addr,
        "date": date_str,
        "subject": subject,
        "tags": sorted(list(tags)) if isinstance(tags, (list, set)) else [],
        "body": body_text,
    }

    if cc_addr:
        sanitized["cc"] = cc_addr

    attachments = extract_attachment_stubs(msg)
    if attachments:
        sanitized["attachments"] = attachments

    return sanitized


def sanitize_thread(
    thread_data: dict[str, Any],
    include_quoted: bool = False,
    max_messages: Optional[int] = None,
) -> dict[str, Any]:
    """Prune a full thread to compact structure with sanitized messages."""
    thread_id = thread_data.get("thread_id") or ""
    subject = thread_data.get("subject") or "(no subject)"
    tags = thread_data.get("tags") or []
    messages = thread_data.get("messages") or []

    if max_messages and max_messages > 0:
        messages = messages[-max_messages:]

    clean_messages = [
        sanitize_message(m, include_quoted=include_quoted)
        for m in messages
    ]

    return {
        "thread_id": thread_id,
        "subject": subject,
        "tags": sorted(list(tags)) if isinstance(tags, (list, set)) else [],
        "message_count": len(clean_messages),
        "messages": clean_messages,
    }


def format_search_results(threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Format search thread entries into lightweight token-efficient summaries."""
    results: list[dict[str, Any]] = []
    for t in threads:
        results.append({
            "thread_id": t.get("thread", t.get("thread_id", "")),
            "date": t.get("date_relative", t.get("date", "")),
            "authors": t.get("authors", ""),
            "subject": t.get("subject", "(no subject)"),
            "tags": t.get("tags", []),
            "matched_messages": t.get("matched", 0),
            "total_messages": t.get("total", 0),
        })
    return results
