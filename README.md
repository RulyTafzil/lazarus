# Notmuch Email Daemon (NED) and Lazarus a qt6 NED client

If for some reason you need local storage to gigabytes of email, lightning fast
tagging/search, and mobile access, then maybe this repo can be of use. This
software is entirely vibe coded, so use at your own risk.

If you want _fast_ tagging and search for lots of local email for free, as far
as I could find, [notmuch](https://notmuchmail.org/) is pretty much the only
game in town. Thankfully, it's also a fantastic option. There are many notmuch
clients out there, unfortunately most haven't been updated in a long time and
don't have "modern" features like rendering html email or easy mobile access.

This project seeks to solve that problem. It separates email management into an
authoritative daemon and lightweight clients:

- **NED (Notmuch Email Daemon)**: A background service that owns the notmuch
  index, serializes Maildir mutations under write locks, manages background IMAP
  syncing, sends email, and broadcasts state updates via Server-Sent Events
  (SSE).
- **Lazarus Desktop**: A responsive PyQt6 GUI client that connects to NED over a
  low-latency Unix domain socket. It provides vim-like keychords, split-pane
  layout with persistent thread previews, and a built-in rich-text compose
  editor.
- **Mobile Web App (PWA)**: A touch-friendly web client served directly by NED
  for phone and tablet use over Tailscale.
- **`ned-client` CLI**: A zero-dependency command-line client for scripting and
  terminal interactions.
- **`ned-mcp`**: Model Context Protocol server exposing account-scoped,
  non-destructive email search, inspection, and triage tools to AI agents.

This project began with just Lazarus, a fork of
[Dodo](https://github.com/akissinger/dodo) by Aleks Kissinger. Lazarus was
modified to include persistent split-pane previews, rich-text composing with
inline images and address autocomplete, mail filter rules, 600+ bundled themes,
and more.

Once Lazarus was in a good place I realized I wanted to use notmuch tagging on
my email all the time, not just locally. Wanting to use notmuch with mobile
email led to the creation of NED.

---

## Core tools

Lazarus acts as a frontend for standard Unix email utilities:

- [notmuch](https://notmuchmail.org/) for indexing, tagging, and fast thread
  searches.
- [mbsync](https://isync.sourceforge.io/) or
  [offlineimap](http://www.offlineimap.org/) to synchronize IMAP accounts with
  local Maildirs.
- [msmtp](https://marlam.de/msmtp/) for outbound SMTP delivery.
- [w3m](http://w3m.sourceforge.net/) for rendering HTML messages to formatted
  plaintext.
- [python-gnupg](https://pypi.org/project/python-gnupg/) for optional PGP
  signing and encryption.

---

## Architecture

```text
               ┌─────────────────────────────────┐
               │    NED (Notmuch Email Daemon)   │
               │  - Owns Notmuch index & Maildir │
               │  - Serialized MutationLock      │
               │  - Background IMAP sync & msmtp │
               │  - SSE invalidation stream      │
               └────────────────┬────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │ Unix domain socket    │ HTTP / WireGuard      │ HTTP / CLI
        ▼                       ▼                       ▼
┌───────────────┐       ┌───────────────┐       ┌───────────────┐
│Lazarus Desktop│       │  Mobile PWA   │       │  ned-client   │
│  (PyQt6 GUI)  │       │(Phone/Tablet) │       │  (CLI/Scripts)│
└───────────────┘       └───────────────┘       └───────────────┘
```

- **Local IPC, Unix domain socket:** Located at `/run/user/$UID/ned/ned.sock`,
  or `~/.local/share/lazarus/ned/ned.sock` as a fallback. Communicates using
  HTTP/1.1 over Unix streams with sub-millisecond latency and operating system
  permission security.
- **Reactive updates via SSE:** When mail arrives or tags change, NED broadcasts
  invalidation events for `threads` and `thread`. Connected clients refresh
  their views immediately without polling.
- **Mutation locking:** All tag modifications and file moves run through NED's
  serialized mutation lock, preventing index concurrency errors between desktop,
  mobile, and background sync operations.

---

## Installation

Three independent distributions come from this repository, pick any combination:

**Headless daemon only, zero Qt dependencies:**

```bash
git clone https://forge.rulytafzil.com/Home/lazarus.git
cd lazarus
pipx install -e ./ned
# installs: ned daemon, ned-client CLI
```

**MCP server for AI agents:**

```bash
pipx install -e ./ned-mcp
# installs: ned-mcp server
```

The daemon reads configuration from `~/.config/ned/config.py` only and serves
clients over a Unix domain socket or Tailscale TCP.

**Desktop GUI with bundled daemon:**

```bash
cd lazarus && pipx install .
```

This installs three executables in your path:

- `lazarus`: Desktop GUI application.
- `ned`: Notmuch Email Daemon, which serves the mobile web client on `/`.
- `ned-client`: CLI utility to interact with NED.

To install desktop application icons and the `.desktop` launcher file:

```bash
lazarus --install-desktop
```

---

## Quick start

### 1. Configuration

Lazarus separates email state from user interface settings:

- The **daemon config** `~/.config/ned/config.py` is the single source of mail
  identity: accounts, From addresses, PGP keys, signatures, send commands, sync
  intervals, and filter rules.
- The **desktop config** `~/.config/lazarus/config.py` controls user interface
  options: themes, fonts, tag icons, layout panes, and key bindings.

#### Configure NED

NED needs to be on the same box that has mbsync, msmtp, and read/write access to
your Maildir.

Run `ned --init-config` to generate a config file automatically at
`~/.config/ned/config.py` Mail and directory information is derived
automatically from your Notmuch, Maildir and msmtp setup. We run
`notmuch config get ####` commands to find your username and email accounts and
maildir location. Then we crawl the maildir to derive sent/draft/trash folder
locations. Assuming those services are setup, there's nothing for you to change.
If Tailscale is configured on the host (for remote / mobile access), NED will
listen on that url / ip.

If you are running NED on the same machine that you are running Lazarus, you can
leave/set the NED_URL value to 127.0.0.1. NED and Lazarus will talk over unix
socket.

NED also spawns a basic mobile web client accessible at the serving url/ip. This
works like any other NED client, using Server Side Events to stay in sync.

#### Configure Lazarus desktop

Lazarus requires `~/.config/lazarus/config.py` to start.

```python
import lazarus.settings as settings

# Interface customization
settings.thread_pane_position = 'right'  # right, left, below, above
settings.init_queries = ['tag:inbox']
settings.search_font_size = 13
settings.message_font_size = 12
```

By default, Lazarus will spawn a NED instance on start.

#### Optional: Connect to NED on another machine

If NED runs on a remote server or another machine on your LAN or Tailnet,
configure Lazarus to connect over the network instead of starting a local
daemon.

Add connection settings to `~/.config/lazarus/config.py`:

```python
import os

os.environ['NED_URL'] = 'https://your-server.your-tailnet.ts.net'  # or http://100.x.y.z:8080
os.environ['NED_TOKEN'] = 'choose-a-secret-token'
```

Alternatively, pass these variables in your shell when launching Lazarus:

```bash
NED_URL="https://your-server.your-tailnet.ts.net" NED_TOKEN="choose-a-secret-token" lazarus
```

When `NED_URL` is set, Lazarus verifies connectivity via `/api/v1/ping`, skips
launching a local NED process, and streams real-time updates over Server-Sent
Events from the remote daemon.

### 2. Start NED

You can run NED in the foreground, in the background, or as a systemd user
service.

In the foreground:

```bash
ned --foreground
```

In the background:

```bash
ned --daemon
```

To run NED automatically on system startup, create a systemd user service file
at `~/.config/systemd/user/ned.service`:

```ini
[Unit]
Description=Notmuch Email Daemon (NED)
After=network.target

[Service]
ExecStart=%h/.local/bin/ned --host 127.0.0.1
Restart=on-failure

[Install]
WantedBy=default.target
```

Enable and start the service:

```bash
systemctl --user daemon-reload
systemctl --user enable --now ned
```

### 3. Launch the desktop GUI

```bash
lazarus
```

Lazarus connects to the local NED socket automatically. If NED is not already
running, Lazarus will start it in the background. If `NED_URL` is set, Lazarus
connects to the remote daemon instead.

---

## Lazarus Desktop interface

Lazarus is intended to be fully keyboard navigable with vim-like commands. Press
`?` inside the app for the full shortcut reference. All hotkeys are editable in
the config.

### Themes

<img src=images/catppucin.webp alt="Catppuccin theme"> <img
src=images/gruvbox.webp alt="Gruvbox theme"> <img src=images/nord.webp alt="Nord
theme">

Lazarus bundles over 600 pre-compiled native themes:

- **Theme picker:** Press `t h` to open the modal command bar with autocomplete,
  or cycle live with `M-<` and `M->`.
- **Persistence:** Selected themes save automatically to
  `~/.config/lazarus/lazarus.conf`.
- **Custom themes:** Place custom 19-key theme JSON files into
  `~/.config/lazarus/themes/`.
- **Theme tools:** Inspect and compile terminal theme definitions using the
  included zero-dependency CLI:

  ```bash
  python tools/import_themes.py --inspect "Gruvbox Material"
  python tools/import_themes.py --compile
  ```

---

## Mail filter rules

Define filter rules in `~/.config/ned/rules.py`, where rules run daemon-side.
Keeping rules in a separate file preserves your filters across
`ned --init-config` runs:

```python
import ned
from ned.rules import Rule

ned.settings.filter_rules = [
    Rule(
        query='from:notifications@github.com',
        tag_add=['github'],
        tag_remove=['inbox'],
        name='GitHub notifications',
    ),
    Rule(
        query='from:billing@',
        tag_add=['bills'],
        move_to='~/Mail/default/Bills',
        name='Bills',
    ),
]
```

Rules execute automatically following each sync cycle, scoped by
`filter_scope_query`, which defaults to `'tag:inbox and tag:unread'`. You can
also trigger them manually with `C-r`.

---

## Post-sync plugins (optional)

NED includes an asynchronous, non-blocking post-sync plugin system. Whenever
mail synchronization (`mbsync` + `notmuch new`) and static filter rules complete,
NED enqueues sync statistics to a dedicated background worker thread.

Key characteristics:
- **Non-blocking**: Mail sync returns immediately. Heavy tasks, external API
  calls, or local heuristics never stall your inbox or hold up subsequent syncs.
- **Live UI updates**: Plugins receive a `PostSyncContext` and can call
  `context.invalidate("threads")` to broadcast Server-Sent Events (SSE). Lazarus
  desktop and mobile clients refresh their views reactively without polling.
- **Standard Library Core**: NED core contains zero vendor or AI dependencies.
  All plugins are decoupled and run from the user's config directory.
- **Logging**: Plugin logs are captured alongside daemon events in
  `~/.local/share/ned/ned.log`.

### Installing and enabling plugins

1. **Install plugin files**:
   Plugins live in your NED configuration directory under
   `~/.config/ned/plugins/<plugin_name>/`. NED automatically prepends
   `~/.config/ned/plugins` to Python's module search path (`sys.path`).

   To install a plugin, copy or clone its folder into your config directory:
   ```bash
   mkdir -p ~/.config/ned/plugins
   cp -r /path/to/my_plugin ~/.config/ned/plugins/
   ```

2. **Enable in `config.py`**:
   In `~/.config/ned/config.py`, add the plugin class or import path to
   `ned.settings.post_sync_plugins`:
   ```python
   ned.settings.post_sync_plugins = [
       "my_plugin.MyPlugin",
   ]
   ```

3. **Restart NED**:
   Restart the daemon to load new plugins and configuration:
   ```bash
   # OpenRC (Alpine):
   rc-service ned restart

   # systemd (User service):
   systemctl --user restart ned
   ```

4. **Verify in logs**:
   Inspect `~/.local/share/ned/ned.log` to watch plugin initialization and
   execution upon sync:
   ```bash
   tail -f ~/.local/share/ned/ned.log
   ```

---

### Writing a custom plugin

Any Python class or callable implementing `on_post_sync(context)` can be used
as a plugin. Create `~/.config/ned/plugins/notify/plugin.py`:

```python
from ned.plugins import PostSyncContext

class NotifyPlugin:
    """Example plugin that inspects new mail and logs or tags threads."""

    def on_post_sync(self, context: PostSyncContext) -> None:
        new_count = context.stats.get("new", 0)
        if new_count == 0:
            return

        context.logger.info("New mail arrived: %d message(s)", new_count)

        # Search for untagged threads
        threads = context.client.search_threads("tag:inbox and tag:unread and not tag:notified")
        for t in threads:
            tid = t.get("thread", t.get("thread_id", ""))
            # Tag the thread
            context.client.modify_tags(threads=[tid], add_tags=["notified"])

        # Broadcast SSE invalidation so desktop and mobile UI refresh
        context.invalidate("threads", reason="plugin:notify")
```

Then enable it in `~/.config/ned/config.py`:
```python
ned.settings.post_sync_plugins = [
    "notify.plugin.NotifyPlugin",
]
```

---

### Example plugin: TypeSafe Jev email classifier

The repository includes a production-ready post-sync plugin in `plugins/typesafe_jev/`
demonstrating two-facet AI email classification using TypeSafe System One (Jev):

- **Strict Privacy**: Transmits strictly email metadata (`from` and `subject`).
  Message bodies are never read, transmitted, or logged.
- **Two-Facet Classification**: Classifies emails across two independent
  taxonomies: Document Type (`Statements`, `Receipts`, `Delivery`, `Support`,
  `Marketing`, `Registrations`, `Urgent`, `Personal`, `ai-trash`) and Domain
  (`Utilities`, `Housing`, `Medical`, `Investments`, `TravelBookings`,
  `TravelRewards`, `StudentLoans`, `Taxes`). Transient informational mail
  (terms of service, policy updates, platform notices) resolves to `"None"`.
- **No Hidden Defaults**: Classification rubrics, categories, confidence thresholds,
  and API key are configured in `taxonomy.py` right inside the plugin directory.

**Setup Instructions**:

1. Copy the plugin to your NED configuration directory:
   ```bash
   mkdir -p ~/.config/ned/plugins
   cp -r plugins/typesafe_jev ~/.config/ned/plugins/
   ```

2. Configure your API key in `~/.config/ned/plugins/typesafe_jev/taxonomy.py`:
   ```python
   API_KEY = "ts_your_actual_api_key"
   ```

3. Enable the plugin in `~/.config/ned/config.py`:
   ```python
   ned.settings.post_sync_plugins = [
       "typesafe_jev.TypeSafeJevPlugin",
   ]
   ```

4. Restart NED and trigger a sync:
   ```bash
   rc-service ned restart   # or systemctl --user restart ned
   ned-client sync
   ```

---

## Multiple accounts

NED and Lazarus supports multiple email accounts with account-specific
signatures. NED should have found your accounts from notmuch and your msmtp
config. You can confirm and configure accounts in the **NED** config
`~/.config/ned/config.py`, which clients like Lazarus discover via the API:

In the Lazarus compose panel, press `[` and `]` to cycle between active sender
accounts, or select them via the dropdown.

---

## Mobile web client and remote access

NED includes a mobile web app with a dark theme, touch gestures, one-tap
archiving, and dynamic signature switching. It's accessible via NED's serve url.
Run `ned --status` to see what url/ip's NED is serving at.

To build your own client, the HTTP API is documented in
[`docs/api.md`](docs/api.md). The running daemon serves a machine-readable
OpenAPI specification at `GET /api/v1/openapi.json`.

### Remote access over Tailscale

Tailscale is used to provide encrypted WireGuard access to NED from remote /
mobile clients. You can set detailed ACL to control access to NED via Tailscale.
If you don't want to use tailscale, you can use anything else you like, just set
NED's `NED_URL` appropriately.

Instructions on how to setup Tailscale: Tailscale provides an encrypted
WireGuard mesh network between your devices without exposing mail ports
publicly. MagicDNS and automated HTTPS Let's Encrypt certificates are included
at no charge on the personal plan.

1. Install Tailscale on your host machine and authenticate:

   ```bash
   tailscale up
   ```

2. Enable HTTPS in the Tailscale admin console under DNS by toggling
   **MagicDNS** and **HTTPS Certificates**.
3. Configure NED to listen on loopback. Tailscale Serve forwards to
   `127.0.0.1:8080` by default. In `~/.config/ned/config.py`:

   ```python
   import ned.settings as settings

   settings.web_host = '127.0.0.1'
   settings.web_port = 8080
   ```

   If running NED via systemd, ensure `~/.config/systemd/user/ned.service`
   passes `--host 127.0.0.1`:

   ```ini
   ExecStart=%h/.local/bin/ned --host 127.0.0.1
   ```
4. Expose NED via Tailscale Serve. Grant operator permissions once so running
   serve does not require root:

   ```bash
   sudo tailscale set --operator=$USER
   ```

   Then route incoming HTTPS traffic to NED's local port in the background:

   ```bash
   tailscale serve --bg 8080
   ```

   Verify the routing status:

   ```bash
   tailscale serve status
   ```

   The output displays your HTTPS URL and target:

   ```text
   https://your-node.tailnet.ts.net (tailnet only)
   |-- / proxy http://127.0.0.1:8080
   ```
5. Open `https://your-node.tailnet.ts.net` in your
   phone browser and install it as a home screen app:
   - **iOS Safari:** Tap Share, then tap **Add to Home Screen**.
   - **Android Chrome:** Tap the three dots menu, then tap **Add to Home
     screen** or **Install app**.

Because HTTPS provides a secure origin, mobile browsers allow full Progressive
Web App features including offline service worker caching and background event
streaming.

---

## The `ned-client` CLI

The `ned-client` command line tool allows scripting and querying NED directly:

```bash
# Health check and connectivity
ned-client ping
ned-client status
ned-client health

# Search threads and inspect thread messages
ned-client search "tag:inbox" --limit 10
ned-client thread "0000000000001234"

# List tags and query address book contacts
ned-client tags
ned-client contacts "Alice"

# Trigger mail synchronization and filter rules
ned-client sync

# Listen to live Server-Sent Events invalidation stream
ned-client events
```

---

## Relationship to Dodo

Lazarus began as a personal fork of [Dodo](https://github.com/akissinger/dodo)
by Aleks Kissinger. Both projects are licensed under the GNU General Public
License v3. Files containing Aleks Kissinger's original code retain his
copyright header, while newly created files carry the Lazarus copyright notice.
See [COPYING](COPYING) for the full license text.
````
