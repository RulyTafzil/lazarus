# NED agentic access and MCP server specification

This document outlines the architecture, required extensions, and design considerations for providing AI agents with access to NED through an expanded `ned-client` CLI and a Model Context Protocol server.

---

## High level architecture

Both the CLI and the MCP server act as consumers of the underlying Python client library in [`ned/client.py`](file:///home/rulyt/Projects/lazarus/ned/client.py).

```text
               ┌─────────────────────────────────────┐
               │         NED Backend Daemon          │
               │  - Notmuch index & Maildir moves    │
               │  - Unix socket & HTTP / Tailscale   │
               └──────────────────┬──────────────────┘
                                  │
                                  ▼
               ┌─────────────────────────────────────┐
               │         NedClient in client.py      │
               │  - Python domain client             │
               │  - Auth, queries, mutations, sync   │
               └──────────────────┬──────────────────┘
                                  │
         ┌────────────────────────┴────────────────────────┐
         │                                                 │
         ▼                                                 ▼
┌──────────────────┐                              ┌──────────────────┐
│  ned-client CLI  │                              │  ned-mcp server  │
│  - Terminal bash │                              │  - MCP SDK tools │
│  - Human output  │                              │  - JSON-RPC      │
└──────────────────┘                              └──────────────────┘
```

The core domain logic stays in `NedClient`. The CLI formats output for humans and shell pipelines, while the MCP server exposes typed tools with JSON Schema definitions for language models over standard input or Server-Sent Events.

---

## Functionality needed to flesh out `ned-client`

[`ned/client.py`](file:///home/rulyt/Projects/lazarus/ned/client.py#L1085-L1115) currently implements only eight read and utility commands: `ping`, `health`, `search`, `thread`, `tags`, `contacts`, `sync`, and `events`. 

To support complete terminal scripting and CLI-based agent access, the following commands must be added:

### 1. Tag mutations
- Command: `ned-client tag <target> --add=<tag> --remove=<tag>`
- Targets: thread ID, message ID, or arbitrary Notmuch query.
- Implementation: calls [`NedClient.modify_tags`](file:///home/rulyt/Projects/lazarus/ned/client.py#L393) or [`NedClient.modify_thread_tags`](file:///home/rulyt/Projects/lazarus/ned/client.py#L427).

### 2. Triage actions
- Command: `ned-client archive <thread_id...>`
  Tags `-inbox -unread` on the specified threads.
- Command: `ned-client move-archive <thread_id...>`
  Moves messages to local archive Maildir and tags `-inbox -unread`.
- Command: `ned-client trash <thread_id...>`
  Moves messages to account Trash Maildir and tags `+trash -inbox -unread`.
- Command: `ned-client restore <thread_id...>`
  Restores messages from Trash back to account INBOX.

### 3. Outbound dispatch and rules
- Command: `ned-client rules`
  Forces immediate execution of daemon filter rules.
- Command: `ned-client send --account=<acct> <message.eml`
  Pipes a finished MIME message to [`NedClient.send_message`](file:///home/rulyt/Projects/lazarus/ned/client.py#L756).

---

## The account scoping problem

### The problem
Currently, Lazarus and Notmuch present all accounts in a single combined view. When an email arrives, it is tagged `inbox` regardless of which account received it. 

For personal desktop use, this unified inbox is convenient. However, giving an AI agent unrestricted access to all email is risky:
- A work assistant should only inspect work mail.
- A personal finance agent should only inspect receipts and bills.
- A public or semi-trusted agent must not read private correspondence.

Because accounts are not currently tracked as distinct Notmuch tags, queries like `tag:inbox` return messages from all configured accounts.

### Three ways to scope accounts

#### Approach 1: Path prefix filtering, zero index changes
In [`ned/actions.py`](file:///home/rulyt/Projects/lazarus/ned/actions.py#L230-L245), mail files are organized by account under Maildir roots, for example `~/Mail/gmail/INBOX` and `~/Mail/work/INBOX`.

Notmuch supports the `path:` search prefix. Any query can be restricted to an account by prepending the Maildir folder path:

```notmuch
(path:work/**) and (tag:inbox and tag:unread)
```

Advantages:
- Requires no database migration or re-indexing.
- Immune to header spoofing, since it checks the actual filesystem storage location.
- Works immediately with existing accounts configured in [`ned.settings.smtp_accounts`](file:///home/rulyt/Projects/lazarus/ned/settings.py#L72).

#### Approach 2: Account tags via daemon filter rules
The daemon can tag incoming mail with its account name during each sync cycle.

In [`ned/rules.py`](file:///home/rulyt/Projects/lazarus/ned/rules.py), add rules matching account storage paths:

```python
Rule(
    name='Tag work account',
    query='path:work/**',
    tag_add=['account:work'],
    tag_remove=[],
)
```

Advantages:
- Clean queries: `tag:account:work and tag:inbox`.
- Visible inside Lazarus desktop as tag badges.

Disadvantages:
- Requires an initial batch tagging pass across existing historical mail.

#### Approach 3: Client and agent sandboxing
The MCP server or CLI can be initialized with an explicit `--account=<name>` parameter.

When scoped to an account:
1. Every search query automatically injects `path:<account>/**`.
2. Any attempt to read or mutate a thread verifies that every message in that thread resides inside the allowed account directory before returning data.
3. Attempts to send mail restrict the sender account to the allowed account name.

This approach provides a reliable sandbox without altering the unified inbox view in the Lazarus desktop client.

---

## MCP server design and considerations

### 1. Token economy and payload pruning
Raw email MIME structures are bloated. A single promotional email can contain megabytes of base64 images, HTML tables, tracking parameters, and dozen-line header trails. Sending raw email JSON to an LLM wastes context tokens and degrades model reasoning.

The MCP server must sanitize payloads before returning them:
- **Header filtering**: Retain only `From`, `To`, `Cc`, `Date`, `Subject`, `Message-ID`, and `Tags`. Strip `Received`, `DKIM-Signature`, `Authentication-Results`, `ARC-*`, and `X-*` headers.
- **Body conversion**: Convert HTML bodies to clean plaintext or markdown using [`ned.html_utils.html_to_plain`](file:///home/rulyt/Projects/lazarus/ned/html_utils.py#L125).
- **Quote collapsing**: By default, return only the newest reply in a message, trimming older nested quotes. Expose a parameter `include_quoted=True` if the agent needs the full thread history.
- **Attachment stubs**: Never pass attachment bytes into LLM context. Replace attachments with structured metadata stubs containing filename, MIME type, and byte count.

### 2. Safety and non-destructive mutations
NED already uses a non-destructive storage design:
- Archiving removes inbox tags or moves files to local archive folders.
- Deleting moves files to account Trash folders and adds the `trash` tag.
- Restoring moves files back to INBOX.

The MCP server preserves this safety net:
- Do not expose permanent expunging or hard file deletion tools to the model.
- If an agent erroneously archives or trashes a message, the action is fully reversible.

### 3. Separation of read, triage, and send capabilities
Agents should be configurable under different permission tiers:

- **Tier 1, Read only**:
  `search_threads`, `get_thread`, `get_message`, `list_tags`, `search_contacts`.
- **Tier 2, Triage**:
  Tier 1 tools plus `archive_thread`, `trash_thread`, `restore_thread`, `apply_tags`.
- **Tier 3, Full access**:
  Tier 2 tools plus `send_email` and `draft_reply`.

Outbound sending should either require explicit user confirmation in the host interface or be confined to drafting.

---

## Tool definitions for the MCP server

The following MCP tools form the core interface:

### `search_threads`
- Arguments:
  - `query`: string, required. Notmuch search query.
  - `account`: string, optional. Restricts search to a specific account directory.
  - `limit`: integer, default 10. Maximum threads to return.
  - `offset`: integer, default 0. Pagination offset.
- Return format: Lightweight thread summaries containing thread ID, date, authors, subject, and tag list.

### `get_thread`
- Arguments:
  - `thread_id`: string, required. Thread identifier.
  - `max_messages`: integer, default 10. Maximum messages to load from the thread.
  - `include_quoted`: boolean, default false. Whether to include older quoted tails in replies.
- Return format: Chronologically ordered messages with essential headers and cleaned plaintext bodies.

### `archive_threads`
- Arguments:
  - `thread_ids`: array of strings, required. Thread IDs to archive.
  - `move_to_local_archive`: boolean, default false. Move files to local archive Maildir instead of tag-only archive.
- Return format: Count of affected threads and status message.

### `trash_threads`
- Arguments:
  - `thread_ids`: array of strings, required. Thread IDs to move to Trash.
- Return format: Count of moved files and updated tags.

### `restore_threads`
- Arguments:
  - `thread_ids`: array of strings, required. Thread IDs to restore from Trash.
- Return format: Count of restored files and updated tags.

### `apply_tags`
- Arguments:
  - `thread_ids`: array of strings, optional. Threads to modify.
  - `message_ids`: array of strings, optional. Individual messages to modify.
  - `add`: array of strings, optional. Tags to add.
  - `remove`: array of strings, optional. Tags to remove.
- Return format: Success confirmation and modified counts.

### `list_tags`
- Arguments: none.
- Return format: List of all tags in the Notmuch index with message counts.

### `draft_reply`
- Arguments:
  - `thread_id`: string, required. Thread to reply to.
  - `body`: string, required. Composed reply text.
  - `to_all`: boolean, default false. Reply to all recipients versus sender only.
  - `account`: string, optional. Specific sending account name.
- Return format: Prepared draft object including recipients, subject, and in-reply-to headers.

---

## Implementation roadmap

1. **Expand `ned.client` CLI subcommands**:
   Add `tag`, `archive`, `trash`, `restore`, `move-archive`, and `send` to `ned/client.py`.

2. **Add account query scoping in `ned.service`**:
   Introduce a helper function `build_account_query(account, query)` that maps account names to their Maildir paths using `settings.mail_root`.

3. **Build the `ned-mcp` package**:
   Create a standalone entry point using the official Python MCP SDK. Allow running via `ned mcp` or `ned-client mcp` with standard input transport.

4. **Integrate token pruning pipeline**:
   Connect body extraction and quote collapsing to the MCP thread formatting layer to ensure minimal context consumption.

5. **Test agent triage workflows**:
   Verify with Claude Code, Cursor, and Antigravity for automated email classification, daily briefing generation, and safe batch triage.
