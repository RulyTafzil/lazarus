# Lazarus Mail Client

## Identity and ecosystem

Lazarus is a keyboard-driven email system for Linux built around Notmuch and Maildir, originally forked from [Dodo](https://github.com/akissinger/dodo) by Aleks Kissinger. The repository maintains three independent packages installable via `pipx`:

| Package | CLI Entry Point | Description |
| :--- | :--- | :--- |
| `lazarus-mail` (`.`) | `lazarus` (`lazarus.app:main`) | PyQt6 and WebEngine desktop client with vim keychords, split-pane preview, and rich text editor. Bundles `ned` and `ned-client`. |
| `ned` (`./ned`) | `ned` (`ned.main:main`), `ned-client` (`ned.client:main`) | Notmuch Email Daemon. Headless background service managing the Notmuch index, Maildir file mutations, IMAP sync, REST and SSE APIs, and mobile PWA assets. Zero Qt dependencies. |
| `ned-mcp` (`./ned-mcp`) | `ned-mcp` (`ned_mcp.server:main`) | Model Context Protocol server exposing account-scoped, non-destructive email inspection and triage tools to AI agents. |

- **Forgejo Repository**: `ssh://forgejo@forge.rulytafzil.com:2222/Home/lazarus.git` (branch `main`)
- **Upstream (untracked)**: `https://github.com/akissinger/dodo.git`

---

## System prerequisites and dependencies

Running Lazarus and its daemon requires several host packages and Python libraries:

1. **Host CLI binaries**:
   - `notmuch`: Required for index queries, tag searches, and message parsing.
   - `mbsync` (`isync`): Required for parallel IMAP sync in [`ned/sync.py`](file:///home/rulyt/Projects/lazarus/ned/sync.py).
   - `msmtp`: Required for outbound SMTP dispatch in [`ned/service.py`](file:///home/rulyt/Projects/lazarus/ned/service.py).
   - `gpg`: Optional, needed only when signing or decrypting PGP messages via [`lazarus/pgp_util.py`](file:///home/rulyt/Projects/lazarus/lazarus/pgp_util.py).

2. **Python dependency tiers**:
   - `ned`: Zero third-party dependencies. Runs on pure Python standard library (`python3 -S` clean).
   - `ned-mcp`: Zero third-party dependencies. Runs on pure Python standard library.
   - `lazarus-mail`: Requires `PyQt6>=6.2`, `PyQt6-WebEngine>=6.2`, `bleach>=5.0`, and optional `python-gnupg`.

---

## Project layout

```text
~/Projects/lazarus/
├── agent.md                  # Agent architecture reference and developer guide
├── AGENTS.md                 # Agent safety policy and permission guidelines
├── MCPServer.md              # Design specification for ned-mcp
├── README.md / COPYING       # Documentation and GPLv3 license
├── setup.py / pyproject.toml # Root build system (lazarus-mail package)
├── mypy.ini / pytest.ini     # Linting and test runner configurations
├── ned_client.py             # Top-level standalone entry point for ned-client CLI
├── ned/                      # Standalone Notmuch Email Daemon (ZERO Qt dependencies)
│   ├── main.py               # `ned` CLI entry point, --init-config, --status
│   ├── daemon.py             # NedDaemon: Unix domain socket + Tailscale TCP listeners
│   ├── handler.py            # NedRequestHandler: HTTP /api/v1/ endpoints and SSE
│   ├── client.py             # NedClient Python client library + `ned-client` CLI
│   ├── concurrency.py        # MutationLock: serialized single-writer queue
│   ├── events.py             # EventBroadcaster: SSE cache invalidation publisher
│   ├── service.py            # Domain query logic, thread assembly, contacts, sending
│   ├── actions.py            # Maildir file move planners, _BulkMoveWorker thread
│   ├── sync.py               # Parallel mbsync coordinator and sync parser
│   ├── notmuch.py            # CLI wrapper around notmuch commands
│   ├── rules.py              # Rule dataclass and filter rule engine
│   ├── openapi.py            # OpenAPI 3.0 specification generator
│   ├── tailscale.py          # Tailscale IP and MagicDNS detection
│   ├── compose_model.py      # ComposeSeed, quote generators, sig_edit placement
│   ├── mime_builder.py       # ComposeData dataclass and multipart MIME builder
│   ├── mail_utils.py         # Body extraction, part parsing, attachment decoding
│   ├── html_utils.py         # HTML to plaintext conversion and linkification
│   ├── signature.py          # Per-account signature file loader
│   ├── settings.py           # Headless daemon settings defaults
│   ├── config.py             # Config validation and rules loader
│   ├── static/               # Mobile PWA web client (index.html, app.js, app.css)
│   └── setup.py              # `ned` standalone distribution
├── lazarus/                  # PyQt6 Desktop Client (imports ned)
│   ├── app.py                # Dodo(QApplication) bootstrap, lifecycle, and desktop entry
│   ├── controller.py         # AppController(QObject): panel registry, sync, SSE bridge
│   ├── mainwindow.py         # MainWindow(QMainWindow): splitter, tabs, status overlay
│   ├── client.py             # Client singleton and ensure_daemon auto-spawner
│   ├── panel.py              # Panel(QWidget) base: keychords, debounce, dirty flags
│   ├── search.py             # SearchPanel + SearchModel + CardDelegate
│   ├── thread.py             # ThreadPanel: double-buffered WebEngine + message tree
│   ├── thread_model.py       # ThreadModel + ThreadItem: conversation and thread modes
│   ├── compose.py            # ComposePanel: fields, account cycling, PGP, attachments
│   ├── editor.py             # RichTextEditor(QTextEdit): inline images, formatting
│   ├── compose_threads.py    # SendmailThread: MIME build and daemon send dispatcher
│   ├── actions.py            # MarkableActionsMixin: panel action bindings to NED
│   ├── tag.py                # TagPanel + TagModel: tag browser with batched counts
│   ├── commandbar.py         # CommandBar(QPlainTextEdit): modal overlay, completions
│   ├── keymap.py             # global_keymap, compose_keymap, tag_keymap
│   ├── address_completer.py  # AddressCompleter(QCompleter) with background loader
│   ├── webengine.py          # MessagePage, EmbeddedImageHandler, request blocker
│   ├── themes.py             # Builtin palettes, pack importer, and QSS generator
│   ├── style.py              # Memoised theme colors and NerdFont glyph resolution
│   ├── config.py / settings.py # Desktop UI settings and validation
│   ├── pgp_util.py           # PGP/MIME encryption and signatures via python-gnupg
│   ├── theme_packs/          # Pre-compiled JSON theme libraries
│   └── icons/                # Bundled hicolor PNG icons (16x16 through 1024x1024)
├── ned-mcp/                  # Standalone Model Context Protocol Server
│   ├── ned_mcp/
│   │   ├── server.py         # Stdio JSON-RPC server and MCP tool declarations
│   │   ├── access.py         # Account path scoping and permission enforcement
│   │   ├── client.py         # MCP transport adapter to NedClient
│   │   └── formatters.py     # Token-saving payload pruners (quotes, HTML, headers)
│   └── setup.py              # `ned-mcp` standalone distribution
├── tests/                    # Pytest test suite covering desktop, daemon, and MCP
├── tools/                    # import_themes.py CLI and terminal mapping definitions
└── contrib/                  # User systemd service (ned.service)
```

---

## Where to look: subsystem guide

When investigating issues or making changes, refer to these primary modules:

### Desktop GUI & Views (`lazarus/`)
- **Window Layout & Splitting**: [`lazarus/mainwindow.py`](file:///home/rulyt/Projects/lazarus/lazarus/mainwindow.py) (`MainWindow`, `QSplitter`, `WatermarkTabWidget`).
- **Panel Base & Vim Keychords**: [`lazarus/panel.py`](file:///home/rulyt/Projects/lazarus/lazarus/panel.py) (`Panel` handles chord buffering and debounce timers).
- **Search & Thread Listing**: [`lazarus/search.py`](file:///home/rulyt/Projects/lazarus/lazarus/search.py) (`SearchPanel`, `SearchModel`, `CardDelegate`, row tinting).
- **Thread Preview & HTML Rendering**: [`lazarus/thread.py`](file:///home/rulyt/Projects/lazarus/lazarus/thread.py) (`ThreadPanel`, double-buffered `QWebEngineView`, `_SwapGuard`) and [`lazarus/webengine.py`](file:///home/rulyt/Projects/lazarus/lazarus/webengine.py) (`EmbeddedImageHandler`).
- **Compose Interface**: [`lazarus/compose.py`](file:///home/rulyt/Projects/lazarus/lazarus/compose.py) (`ComposePanel`, field toggles, account switching) and [`lazarus/editor.py`](file:///home/rulyt/Projects/lazarus/lazarus/editor.py) (`RichTextEditor`).
- **Tag Browser**: [`lazarus/tag.py`](file:///home/rulyt/Projects/lazarus/lazarus/tag.py) (`TagPanel`, `TagModel`).
- **Modal Command Input**: [`lazarus/commandbar.py`](file:///home/rulyt/Projects/lazarus/lazarus/commandbar.py) (`CommandBar`, search/tag/theme picker).
- **Application Orchestration**: [`lazarus/controller.py`](file:///home/rulyt/Projects/lazarus/lazarus/controller.py) (`AppController`, SSE watcher thread, panel routing).
- **Keybindings**: [`lazarus/keymap.py`](file:///home/rulyt/Projects/lazarus/lazarus/keymap.py) (`global_keymap`, `compose_keymap`, `tag_keymap`, `COMPOSE_ALLOWED_GLOBALS`). User hotkeys are defined here and customized in `~/.config/lazarus/config.py`.
- **Themes & Palettes**: [`lazarus/themes.py`](file:///home/rulyt/Projects/lazarus/lazarus/themes.py) (QSS template assembly) and [`lazarus/style.py`](file:///home/rulyt/Projects/lazarus/lazarus/style.py).

### Daemon Engine & Services (`ned/`)
- **HTTP/REST Endpoints & SSE**: [`ned/handler.py`](file:///home/rulyt/Projects/lazarus/ned/handler.py) (`NedRequestHandler`, route routing, auth check) and [`ned/events.py`](file:///home/rulyt/Projects/lazarus/ned/events.py) (`EventBroadcaster`).
- **Network Listeners & IPC**: [`ned/daemon.py`](file:///home/rulyt/Projects/lazarus/ned/daemon.py) (`NedDaemon`, Unix socket `/run/user/$UID/ned/ned.sock`, Tailscale TCP).
- **Concurrency & Locking**: [`ned/concurrency.py`](file:///home/rulyt/Projects/lazarus/ned/concurrency.py) (`MutationLock` serializes Xapian write operations).
- **Maildir Operations & Trashing**: [`ned/actions.py`](file:///home/rulyt/Projects/lazarus/ned/actions.py) (`_BulkMoveWorker`, trash/archive planning, UID strip, expunge).
- **Notmuch CLI Abstraction**: [`ned/notmuch.py`](file:///home/rulyt/Projects/lazarus/ned/notmuch.py) (subprocesses for search, show, count, tag, address).
- **Sync & Filtering**: [`ned/sync.py`](file:///home/rulyt/Projects/lazarus/ned/sync.py) (parallel `mbsync`, `notmuch new`) and [`ned/rules.py`](file:///home/rulyt/Projects/lazarus/ned/rules.py) (`Rule`, `apply_rules`).
- **MIME Generation & Signatures**: [`ned/mime_builder.py`](file:///home/rulyt/Projects/lazarus/ned/mime_builder.py), [`ned/compose_model.py`](file:///home/rulyt/Projects/lazarus/ned/compose_model.py) (`sig_edit`), and [`ned/signature.py`](file:///home/rulyt/Projects/lazarus/ned/signature.py).
- **Python Client API**: [`ned/client.py`](file:///home/rulyt/Projects/lazarus/ned/client.py) (`NedClient`, Unix socket and HTTP transport implementations).

### AI Agent Integration (`ned-mcp/`)
- **MCP Server Entry & Tools**: [`ned-mcp/ned_mcp/server.py`](file:///home/rulyt/Projects/lazarus/ned-mcp/ned_mcp/server.py) (Stdio JSON-RPC protocol, tool registry).
- **Account Sandboxing & Permissions**: [`ned-mcp/ned_mcp/access.py`](file:///home/rulyt/Projects/lazarus/ned-mcp/ned_mcp/access.py) (enforces `path:<account>/**`, permission flags).
- **Payload Pruning**: [`ned-mcp/ned_mcp/formatters.py`](file:///home/rulyt/Projects/lazarus/ned-mcp/ned_mcp/formatters.py) (quote folding, HTML strip, attachment metadata).

---

## Architecture & data flow

### Desktop client architecture
The desktop is purely a consumer of the daemon:
- It **never** shells out to `notmuch` directly.
- It **never** invokes `msmtp` or writes to Maildir files directly.
- It **never** opens or parses local Maildir email files or `~/.config/ned` configs on disk. All reply seeds, references, signatures, and CID images route through the NED API.
- On startup, [`lazarus/client.py:ensure_daemon`](file:///home/rulyt/Projects/lazarus/lazarus/client.py) verifies NED is running, spawning it as a child process if necessary when local socket access is configured.
- Mutations dispatch via `NedClient` over the Unix domain socket or remote HTTP/Tailscale connection.
- State changes invalidate desktop views via a background Server-Sent Events (SSE) listener (`_NedEventBridge` in [`lazarus/controller.py`](file:///home/rulyt/Projects/lazarus/lazarus/controller.py)), debounced to 150ms.
- Preview selection advance: When deleting or archiving a thread shown in the preview pane, [`SearchPanel._advance_selection`](file:///home/rulyt/Projects/lazarus/lazarus/search.py) moves the selection to the adjacent thread and loads it immediately, keeping the preview pane open.

```text
┌─────────────────────────────────────────────────────────────┐
│ MainWindow (QSplitter)                                      │
│ ┌───────────────────────┐   ┌─────────────────────────────┐ │
│ │ QTabWidget            │   │ QStackedWidget (preview)    │ │
│ │  ├─ SearchPanel       │   │  ├─ ThreadPanel             │ │
│ │  ├─ ComposePanel      │   │  │   └─ QWebEngineView      │ │
│ │  └─ TagPanel          │   │  └─ QLabel placeholder      │ │
│ └───────────────────────┘   └─────────────────────────────┘ │
│ CommandBar (modal overlay) / StatusBar                      │
└─────────────────────────────────────────────────────────────┘
```

### NED Daemon & API Layer
- **Mutation Boundary**: While Notmuch allows concurrent readers, the underlying Xapian database allows only one writer. [`MutationLock`](file:///home/rulyt/Projects/lazarus/ned/concurrency.py) serializes all tag updates, Maildir moves, and sync operations into a single-writer queue.
- **REST Conventions**: Formally documented in [`docs/api.md`](file:///home/rulyt/Projects/lazarus/docs/api.md) and served live at `GET /api/v1/openapi.json`. Read endpoints return raw Notmuch JSON structures. Mutation endpoints return status envelopes (`{"status": "ok", "ok": true, ...}`).
- **Cache Invalidation via SSE**: `GET /api/v1/events` streams cache invalidations (`{"scope": "threads"|"thread", "id": ..., "reason": ...}`). Desktop and web clients invalidate and re-query views rather than patching state locally.
- **Mobile PWA Web App**: When the TCP listener is enabled, the daemon serves the bundled mobile PWA web assets from [`ned/static/`](file:///home/rulyt/Projects/lazarus/ned/static/) at `GET /`. The PWA interacts with the exact same `/api/v1/` REST and SSE endpoints as the desktop client.

### `ned-mcp` Security & Sandboxing Model
The MCP server enforces strict controls for AI agents:
1. **Maildir Path Scoping**: All queries are automatically wrapped with account path constraints: `(path:<account>/**) and (<query>)`. Cross-account leakage is prevented at the filesystem level.
2. **Deny-by-Default Capabilities**:
   - Reading (`search_threads`, `get_thread`, `list_tags`) is the default baseline.
   - Tagging (`apply_tags`) requires `--tags <list|*>`.
   - Trashing and restoring (`trash_threads`, `restore_threads`) requires `--allow-trash`.
   - Archiving (`archive_threads`) requires `--allow-archive` or `--allow-archive-to-local`.
   - Sending (`send_email`) requires `--allow-send`.
3. **Zero Expunge**: Hard message deletion (`expunge`) is not exposed. Trashing moves messages to the account Trash directory with `+trash -inbox -unread`.
4. **Token Economy**: Message payloads are aggressively compacted:
   - Deeply nested reply chains are folded with `[quoted text hidden]`.
   - HTML parts are rendered to plaintext.
   - Transit headers are stripped, leaving only RFC 5322 essentials (`From`, `To`, `Subject`, `Date`, `Message-ID`).
   - Attachments are stubbed with filename, MIME type, and byte size.

---

## Configuration

Configurations are decoupled into separate files:

| Configuration File | Read By | Purpose |
| :--- | :--- | :--- |
| `~/.config/lazarus/config.py` | Desktop GUI (`lazarus.settings`) | UI-only preferences: theme, fonts, hotkey definitions, `search_list_mode` ('list' or 'card'), external browser commands. |
| `~/.config/ned/config.py` | Daemon (`ned.settings`) | Mail routing: `email_address`, `smtp_accounts`, `sent_dir`, `send_mail_command`, `sync_mail_interval`, `web_token`. Auto-generate with `ned --init-config`. |
| `~/.config/ned/rules.py` | Daemon (`ned.config.load_rules`) | Custom filter rules: `Rule(name, query, add_tags, remove_tags, move_to)`. |
| `~/.config/ned/<acct>/signature(.html)` | Daemon (`ned.signature`) | Account plain and HTML signatures loaded on demand. |

---

## Storage layout and tag lifecycle

Lazarus and NED organise local mail in Maildir format under a root folder (defaulting to `~/Mail`, configurable via `database.mail_root` in Notmuch or `settings.mail_root`).

### Directory hierarchy
- `~/Mail/<account>/INBOX/cur/` and `new/`: Incoming messages synchronized by `mbsync`.
- `~/Mail/<account>/Trash/cur/`: Trashed messages for a specific account.
- `~/Mail/Archive/cur/`: Local multi-account archive folder.
- `~/Mail/<account>/Sent/cur/`: Outgoing messages stored after successful `msmtp` dispatch.

### Message identity and UID handling
When moving files between folders (such as moving from INBOX to Trash), [`ned/actions.py`](file:///home/rulyt/Projects/lazarus/ned/actions.py) strips `mbsync` UID annotations (`",U=<num>"`) from the destination filename. This prevents duplicate UID conflicts when `mbsync` synchronizes changes back to remote IMAP servers. The daemon resolves stale paths by matching file stems across `cur/` and `new/` directories to prevent races with concurrent background flag sync.

### Tag states and lifecycle
- **New mail**: Synced via `POST /api/v1/sync` or background timers. `notmuch new` indexes files, then [`ned/rules.py`](file:///home/rulyt/Projects/lazarus/ned/rules.py) runs configured filter rules. Initial state typically includes `inbox` and `unread`.
- **Marking**: Triage operations stage messages with the `marked` tag for bulk tagging, trashing, or archiving.
- **Archiving**:
  - Virtual archive: Removes `inbox` and `unread` without moving files on disk.
  - Local disk archive (`POST /api/v1/move-archive`): Relocates files to `~/Mail/Archive/` and strips `inbox` and `unread`.
- **Trashing (`POST /api/v1/trash`)**: Relocates files to the account Trash directory, removes `inbox` and `unread`, and adds `trash`.
- **Restoring (`POST /api/v1/restore`)**: Relocates files from Trash back to the account INBOX, removes `trash`, and adds `inbox`.
- **Expunging (`POST /api/v1/expunge`)**: Irreversible step that appends the Maildir `T` (trashed) flag suffix to every file matching `tag:trash`. The next `mbsync` run purges these messages permanently from remote IMAP stores.

---

## Running and debugging the daemon

The desktop client spawns NED automatically through [`lazarus/client.py:ensure_daemon`](file:///home/rulyt/Projects/lazarus/lazarus/client.py). During development or when running on headless servers, you can run and control NED directly from the command line.

### CLI commands and flags
- `ned`: Starts the daemon process in the foreground.
- `ned --status`: Verifies whether the Unix domain socket and TCP port are responding, prints bind addresses, and displays recommended remote client URLs.
- `ned --init-config`: Scans local Notmuch and Maildir setups, writes an initial `~/.config/ned/config.py`, and creates sample rules in `~/.config/ned/rules.py`.
- `ned --log-level [DEBUG|INFO|WARNING|ERROR]`: Adjusts logging verbosity (default is `INFO`). Run with `--log-level DEBUG` for full HTTP request and lock traces.
- `ned --socket <path>`: Overrides the Unix domain socket location (default: `/run/user/$UID/ned/ned.sock`).
- `ned --host <ip>` and `--port <int>`: Binds the HTTP listener (default port `8080`). NED auto-detects Tailscale IP and MagicDNS.
- `ned --no-tcp`: Restricts the daemon strictly to the Unix domain socket.
- `ned --token <secret>`: **Deprecated.** Bearer token for **non-browser** HTTP clients via the `Authorization: Bearer` header only. The legacy `?token=` query parameter is removed and the web/PWA client no longer supports tokens (browser SSE / page navigation cannot send headers). Remote access relies on Tailscale ACLs; the TCP listener also enforces Host-header and Origin checks for browser requests.
- `ned --allow-insecure`: Overrides the guard that prevents binding an unauthenticated TCP listener to non-loopback interfaces.
- `ned --sync-interval <seconds>`: Configures periodic background mail sync (-1 disables).

### Environment variables
- `NED_SOCK`: Overrides the Unix domain socket path used by [`lazarus/client.py`](file:///home/rulyt/Projects/lazarus/lazarus/client.py) or `ned-client`.
- `NED_URL`: Base HTTP URL to connect desktop clients to a remote daemon (for example, `export NED_URL="http://100.x.y.z:8080"`).
- `NED_TOKEN`: Bearer token passed in the `Authorization` header when using `NED_URL`.
- `LAZARUS_DISABLE_NED=1`: Prevents the desktop application from automatically spawning the daemon. Useful in headless environments or isolated testing.

---

## Durable invariants & development rules

1. **Daemon Headless Separation**: The `ned/` and `ned-mcp/` packages must contain **zero Qt imports**. Background threads in daemon packages must use `threading.Thread`, never `QThread`. Verify with `python3 -S`.
2. **Pure Client Invariant**: The desktop package (`lazarus/`) must remain a pure NED API client. It must never inspect local Maildir files, read `~/.config/ned`, or assume the daemon runs on the same physical filesystem.
3. **Compose Closed Key Surface**: Compose intercepts and swallows global navigation keys (`j`, `k`, `d`, `a`, etc.) via `ComposePanel._allow_global_key` and [`COMPOSE_ALLOWED_GLOBALS`](file:///home/rulyt/Projects/lazarus/lazarus/keymap.py). List actions must never trigger on background panels while typing.
4. **One-Directional Escape in Compose**: `<escape>` inside `ComposePanel` moves focus out of text fields into the outer chrome. It must **never** toggle back into the editor.
5. **Structural Signature Placement**: Signatures are injected structurally using [`compose_model.sig_edit`](file:///home/rulyt/Projects/lazarus/ned/compose_model.py) above `ComposeSeed.quoted_tail`. Never scan for marker strings or append blindly to prevent newline drift.
6. **Theme Palette Invariant**: Every theme must define all 19 keys in `THEME_KEYS`. Fallbacks must resolve through [`style.theme_color_or()`](file:///home/rulyt/Projects/lazarus/lazarus/style.py).
7. **CardDelegate Contrast**: When painting custom list items, never paint opaque `bg_highlight` without color adjustment. Use a 25% alpha blend wash over `bg` so distinct text colors (`fg_from`, `fg_subject_unread`, `fg_tags`) remain legible.
8. **Thread Preview Double-Buffering**: Rapid toggles between HTML and plaintext create multiple in-flight loads. [`_SwapGuard`](file:///home/rulyt/Projects/lazarus/lazarus/thread.py) ensures only the most recent request swaps into view.
9. **Offscreen Pytest Safety**:
   - Constructing `QWebEngineView` or `QWebEnginePage` in offscreen Qt segfaults pytest. Test models and interceptors using stubs; only instantiate bare scheme handlers.
   - All displayed Qt widgets in tests must be deleted at test end via `deleteLater()` fixtures to prevent garbage collection crashes during subsequent test runs.
   - Tests use the `client_stub` fixture; panels must never hit a live daemon socket during standard test runs.
10. **URL Segment Unquoting**: Browser and web clients percent-encode URL parameters (e.g. `@` as `%40` in Message-IDs). Always unquote URL segments with `urllib.parse.unquote()` before querying Notmuch.
11. **Desktop Packaging**: Desktop integration icons and `.desktop` files are managed exclusively via `lazarus --install-desktop` copying package data into `~/.local/share`. Do not use `data_files` in `setup.py`.
12. **Type Checking & Tests**:
    - Tests: Run `python -m pytest` via the project environment (such as `~/.local/share/pipx/venvs/lazarus-mail/bin/python -m pytest` or within an active virtualenv). All test suites must pass before submitting changes.
    - Types: Run `mypy lazarus ned` under `disallow_untyped_defs = True` (configured in [`mypy.ini`](file:///home/rulyt/Projects/lazarus/mypy.ini)). When running mypy outside the GUI virtualenv, pass `--python-executable` pointing to the Python environment containing PyQt6 so types resolve correctly.
13. **Git Workflow**: Mainline branch is `main`. Never commit directly to `main`. Create feature branches (`pr/<slug>`), push to Forgejo (`forge.rulytafzil.com:2222`), and submit PRs.
