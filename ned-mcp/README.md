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

Run `ned-mcp` as an MCP server over standard input and standard output (JSON-RPC stdio transport):

```bash
# Work account with tag whitelist (read and triage only)
ned-mcp --account work --allow-tags todo,followup,archive

# Personal account with full tag access and outbound sending enabled
ned-mcp --account personal --full-tags --allow-send

# Connect to a remote NED daemon over Tailscale HTTP
ned-mcp --account work --url http://100.x.y.z:8080 --token secret123
```

## Configuration for MCP Clients

### Claude Desktop / Claude Code (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "work-mail": {
      "command": "ned-mcp",
      "args": [
        "--account", "work",
        "--allow-tags", "todo,followup,reviewed"
      ]
    }
  }
}
```

### Antigravity / Cursor / Custom Agent

```json
{
  "mcpServers": {
    "ned": {
      "command": "ned-mcp",
      "args": [
        "--account", "primary",
        "--full-tags"
      ]
    }
  }
}
```
