# ned-mcp: Model Context Protocol Server for NED

`ned-mcp` is the Model Context Protocol (MCP) server for the Notmuch Email Daemon (NED). It allows AI agents, coding assistants, and automated workflows to search, read, and triage emails safely.

## Key Features

1. **Account sandboxing**: Access is strictly limited to specified accounts using filesystem Maildir path scoping (`path:<account>/**`). An agent scoped to `work` can never query or read personal emails.
2. **Account-based access controls**: Granular permissions per account:
   - Tag whitelisting (e.g. only allow modifying `todo` and `followup`).
   - Full tag permissions when explicitly enabled.
   - Outbound send disabled by default; enabled only with `--allow-send`.
3. **Zero expunge**: Hard message deletion (`expunge`) is permanently blocked. Email removals only move files to account Trash folders using non-destructive IMAP conventions.
4. **Token economy**: Automatic payload pruning:
   - Nested reply quotes collapsed by default.
   - HTML converted to clean plaintext.
   - Attachments replaced with lightweight metadata stubs.
   - Bloated transit headers stripped.
5. **Standalone package**: Installable independently from Lazarus or NED via `pipx`.

## Installation

```bash
# Standalone install using pipx
pipx install ./ned-mcp

# Or development install
pip install -e ./ned-mcp
```

## Usage

Run `ned-mcp` as an MCP server over standard input and standard output using JSON-RPC stdio transport:

```bash
# Read-only account (default baseline)
ned-mcp --account bot@example.com

# Triage account with trash, archive, and whitelisted tags
ned-mcp --account work --allow-trash --allow-archive --tags todo,followup,reviewed

# Full personal account with all permissions enabled
ned-mcp --account personal --tags "*" --allow-trash --allow-archive --allow-send

# Connect to a remote NED daemon over Tailscale HTTP
ned-mcp --account work --url https://mail-server.example.ts.net --token secret123
```

## Access Controls and Configuration

`ned-mcp` follows a deny-by-default security model. Baseline access is strictly read-only, scoped to the specified account Maildir directory tree via `path:<account>/**`. All mutations require express opt-in flags. Hard deletion, or expunge, is permanently blocked.

### Permissions Reference

| Flag | Description | Default |
| :--- | :--- | :--- |
| `--account <name>` | Account name to scope access to. Accepts repeated flags or comma-separated lists like `work,personal`. | Required |
| Baseline (no extra flags) | Grants `search_threads`, `get_thread`, and `list_tags`. Mutation tools are omitted. | Read-only |
| `--tags <list\|*>` | Enables `apply_tags`. Provide comma-separated tags like `todo,followup` or `*` for full tag access. | No tag mutation |
| `--allow-trash` | Enables `trash_threads` and `restore_threads` for non-destructive removals via `+trash -inbox -unread`. | Disabled |
| `--allow-archive` | Enables `archive_threads` for tag-only archiving with `-inbox -unread`. | Disabled |
| `--allow-archive-to-local` | Enables `archive_threads` with disk relocation to the local Archive Maildir folder. | Disabled |
| `--allow-send` | Enables `send_email` using the account SMTP credentials. | Disabled |
| `--url <url>` | Remote NED daemon base URL, for example over Tailscale HTTP. Can also use `NED_URL`. | Local socket |
| `--token <token>` | Bearer authentication token for remote server. Can also use `NED_TOKEN`. | None |

### Sample MCP Client Configuration in `claude_desktop_config.json`

This single configuration block demonstrates read-only, triage with trash and selective tags, full access, and remote connection side by side:

```json
{
  "mcpServers": {
    "bot-audit": {
      "command": "ned-mcp",
      "args": [
        "--account", "bot@example.com"
      ]
    },
    "work-triage": {
      "command": "ned-mcp",
      "args": [
        "--account", "work",
        "--allow-trash",
        "--allow-archive",
        "--tags", "todo,followup,reviewed"
      ]
    },
    "personal-full": {
      "command": "ned-mcp",
      "args": [
        "--account", "personal",
        "--tags", "*",
        "--allow-trash",
        "--allow-archive",
        "--allow-send"
      ]
    },
    "remote-account": {
      "command": "ned-mcp",
      "args": [
        "--account", "remote",
        "--allow-trash"
      ],
      "env": {
        "NED_URL": "https://mail-server.example.ts.net",
        "NED_TOKEN": "secret-bearer-token"
      }
    }
  }
}
```

- **`bot-audit`, read-only account**: Omits all mutation flags. Registers only `search_threads`, `get_thread`, and `list_tags`.
- **`work-triage`, read, trash, archive, and selective tags**: Opts into `--allow-trash`, `--allow-archive`, and whitelisted tags. Outbound sending is disabled.
- **`personal-full`, full access**: Opts into all operations with `--tags *`, `--allow-trash`, `--allow-archive`, and `--allow-send`.
- **`remote-account`, remote daemon**: Connects to a remote NED daemon over Tailscale HTTP using environment variables for URL and Bearer token.

---

## Companion Daemon Filter Rules in `rules.py`

While `ned-mcp` permissions control what an AI agent can do, daemon filter rules in `~/.config/ned/rules.py` run automatically in the background during mail synchronization:

```python
from ned.rules import Rule

RULES = [
    # 1. Account path tagging rule for ingest and tagging
    Rule(
        name="Tag bot account",
        query="path:bot@example.com/** and not tag:bot",
        tag_add=["bot"],
        tag_remove=[],
    ),

    # 2. Automated newsletter filing rule with tag and Maildir move
    Rule(
        name="Mute and archive automated notifications",
        query="from:notifications@github.com or from:no-reply@",
        tag_add=["notifications"],
        tag_remove=["inbox", "unread"],
        move_to="~/Mail/Archive",
    ),

    # 3. High priority sender rule for tags
    Rule(
        name="Mark urgent correspondence",
        query="from:boss@example.com or subject:URGENT",
        tag_add=["priority", "flagged"],
        tag_remove=[],
    ),

    # 4. Spam or junk routing rule with tag and trash move
    Rule(
        name="Filter junk",
        query="subject:'marketing promotion' or from:spam@example.com",
        tag_add=["trash"],
        tag_remove=["inbox", "unread"],
        move_to="/mnt/Mail/bot@example.com/Trash",
    ),
]
```


