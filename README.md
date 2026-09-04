# Lazarus

Lazarus is a fast, keyboard-driven email environment built on [notmuch](https://notmuchmail.org/).

It separates email management into an authoritative daemon and lightweight clients:

- **NED (Notmuch Email Daemon)**: A background service that owns the notmuch index, serializes Maildir mutations under write locks, manages background IMAP syncing, sends email, and broadcasts state updates via Server-Sent Events (SSE).
- **Lazarus Desktop**: A responsive PyQt6 GUI client that connects to NED over a low-latency Unix domain socket. It provides vim-like keychords, split-pane layout with persistent thread previews, and a built-in rich-text compose editor.
- **Mobile Web App (PWA)**: A touch-friendly web client served directly by NED for phone and tablet use over Tailscale.
- **`ned-client` CLI**: A zero-dependency command-line client for scripting and terminal interactions.

Lazarus began as a fork of [Dodo](https://github.com/akissinger/dodo) by Aleks Kissinger. Today it includes an independent daemon architecture, persistent split-pane previews, rich-text composing with inline images and address autocomplete, mail filter rules, 600+ bundled themes, and mobile access.

---

## Core tools

Lazarus acts as a frontend for standard Unix email utilities:

- [notmuch](https://notmuchmail.org/) for indexing, tagging, and fast thread searches.
- [mbsync](https://isync.sourceforge.io/) or [offlineimap](http://www.offlineimap.org/) to synchronize IMAP accounts with local Maildirs.
- [msmtp](https://marlam.de/msmtp/) for outbound SMTP delivery.
- [w3m](http://w3m.sourceforge.net/) for rendering HTML messages to formatted plaintext.
- [python-gnupg](https://pypi.org/project/python-gnupg/) for optional PGP signing and encryption.

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

- **Local IPC (Unix domain socket):**
  Located at `/run/user/$UID/ned/ned.sock` (or `~/.local/share/lazarus/ned/ned.sock`). Communicates using HTTP/1.1 over Unix streams with sub-millisecond latency and operating system permission security.
- **Reactive updates via SSE:**
  When mail arrives or tags change, NED broadcasts invalidation events (`threads`, `thread`). Connected clients refresh their views immediately without polling.
- **Mutation locking:**
  All tag modifications and file moves run through NED's serialized mutation lock, preventing index concurrency errors between desktop, mobile, and background sync operations.

---

## Installation

Two independent distributions come from this repository — pick either or both:

**Headless daemon only (zero Qt dependencies):**
```bash
git clone https://forge.rulytafzil.com/Home/lazarus.git
cd lazarus
pipx install -e ./ned
# installs: ned (daemon), ned-client (CLI)
```

The daemon reads configuration from `~/.config/ned/config.py` only (generate from your desktop config with `ned --init-config`) and serves any client over a Unix domain socket or Tailscale TCP.

**Desktop GUI (+ bundled daemon):**
```bash
cd lazarus && pipx install .
```

This installs three executables in your path:
- `lazarus`: Desktop GUI application.
- `ned`: Notmuch Email Daemon (serves the mobile web client on `/`).
- `ned-client`: CLI utility to interact with NED.

To install desktop application icons and the `.desktop` launcher file:

```bash
lazarus --install-desktop
```

---

## Quick start

### 1. Configuration

The **daemon** config `~/.config/ned/config.py` is the single source of mail
identity: accounts, From addresses, PGP keys, signatures, send, sync, and
filter rules. Generate it from an existing desktop config with
`ned --init-config`.

The **desktop** config `~/.config/lazarus/config.py` is UI-only (themes,
fonts, tags, keymap) — the compose panel pulls accounts/signatures from the
NED API and sends through the daemon, so no mail settings are needed here
(any old `email_address`/`smtp_accounts`/`sent_dir` lines can simply be
removed).

Set your email identity in the ned config (example):

```python
import ned.settings as settings

# Required settings
settings.email_address = 'Your Name <you@example.com>'
settings.smtp_accounts = ['default']
settings.sent_dir = '~/Mail/default/Sent'

# Common optional settings
settings.mail_root = '~/Mail'
settings.archive_dir = '~/Mail/Archive'
```

### 2. Start NED

You can run NED in the foreground, in the background, or as a systemd user service.

In the foreground:

```bash
ned --foreground
```

In the background:

```bash
ned --daemon
```

To run NED automatically on system startup, create a systemd user service file at `~/.config/systemd/user/ned.service`:

```ini
[Unit]
Description=Notmuch Email Daemon (NED)
After=network.target

[Service]
ExecStart=%h/.local/bin/ned --foreground
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

Lazarus connects to the local NED socket automatically. If NED is not already running, Lazarus will start it in the background.

---

## Desktop interface

Everything in Lazarus can be operated from the keyboard. Press `?` inside the app for the full shortcut reference.

### Layout

Lazarus uses a split-pane layout with an email list on one side and a persistent thread preview on the other. You can change preview placement with `thread_pane_position` (`right`, `left`, `below`, or `above`).

- `j` / `k` (or `↓` / `↑`): Move between threads in the list. The thread under the cursor opens in the preview pane after a short delay.
- `J` / `K`: Move between individual messages within the open thread.
- `h` / `l`: Switch between open tabs.
- `<enter>`: Focus the thread preview.
- `<escape>`: Return focus to the thread list.
- `C-<enter>`: Close the thread preview.
- `c`: Compose a new message.
- `r` / `R`: Reply / reply-all (contextual: replies to the focused message in preview, or the list's selected thread).
- `C-y`: Forward the current message or thread.
- `T`: Open the tag browser.
- `` ` ``: Trigger background mail sync.
- `C-r`: Re-apply mail filter rules.

### Tagging and triage

Actions operate on marked batches if marked threads exist, or fall back to the selected thread:

- `s`: Mark thread and advance the cursor.
- `u` / `f`: Toggle unread / flagged status.
- `1` through `9`: Toggle tags configured in `tag_hotkeys`.
- `t t`: Tag current thread.
- `t m`: Tag all marked threads.
- `a`: Archive (removes `inbox` and `unread` tags; requires at least one other tag to prevent orphaned mail).
- `A`: Archive to local Maildir (`archive_dir`).
- `d`: Move thread to Trash folder.
- `d u`: Restore thread from Trash back to INBOX.
- `d d`: Empty trash permanently (irreversible).

All actions route through NED, which serializes file moves and runs `notmuch new` automatically.

### Composing

<img src=images/compose.webp alt="Lazarus compose panel">

Lazarus includes a built-in rich-text compose editor:

- **Formatting toolbar:** Bold, italic, underline, lists, alignment, font colors, and inline images using NerdFont glyphs (or `Ctrl+B`, `Ctrl+I`, `Ctrl+U`).
- **Plaintext mode:** Toggle with `Shift+H` or the `[Plaintext | HTML]` toolbar button to send standard `text/plain` emails without an HTML part.
- **Inline images and attachments:** Paste or drag-and-drop images directly into the body. Add file attachments with `a`.
- **Address autocomplete:** Recipient suggestions populate from your notmuch address history.
- **Cc and Bcc rows:** Reveal or hide extra header rows with `M-c` and `M-b`.
- **Signatures:** Per-account signatures insert automatically above quoted text (`~/.config/ned/<account>/signature` or `signature.html`; served to the desktop via the NED API).
- **Multiple accounts:** Switch sending accounts with `[` / `]` or the From dropdown. The From header and signature update instantly.
- **PGP:** Toggle signing with `p` and encryption with `e` (requires a per-account `gnupg_keyid` in the ned config).
- **Send:** `C-s` builds the message locally and hands it to NED, which runs msmtp, saves the sent copy, and indexes it. `<escape>` exits to the panel chrome.

### Themes

<img src=images/catppucin.webp alt="Catppuccin theme"> <img src=images/gruvbox.webp alt="Gruvbox theme"> <img src=images/nord.webp alt="Nord theme">

Lazarus bundles over 600 pre-compiled native themes:

- **Theme picker:** Press `t h` to open the modal command bar with autocomplete, or cycle live with `M-<` and `M->`.
- **Persistence:** Selected themes save automatically to `~/.config/lazarus/lazarus.conf`.
- **Custom themes:** Place custom 19-key theme JSON files into `~/.config/lazarus/themes/`.
- **Theme tools:** Inspect and compile terminal theme definitions using the included zero-dependency CLI:
  ```bash
  python tools/import_themes.py --inspect "Gruvbox Material"
  python tools/import_themes.py --compile
  ```

---

## Mail filter rules

Define filter rules in the **NED** config (`~/.config/ned/config.py` — rules
run daemon-side):

```python
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

Rules execute automatically following each sync cycle, scoped by `filter_scope_query` (default `'tag:inbox and tag:unread'`). You can also trigger them manually with `C-r`.

---

## Multiple accounts

Configure multiple accounts in the **NED** config (`~/.config/ned/config.py`) —
the desktop compose discovers them via the API:

```python
ned.settings.smtp_accounts = ['personal', 'work']
ned.settings.email_address = {
    'personal': 'Me <me@personal.com>',
    'work': 'Me <me@work.com>',
}
ned.settings.sent_dir = {
    'personal': '~/Mail/personal/Sent',
    'work': '~/Mail/work/Sent',
}
```

In the compose panel, press `[` and `]` to cycle between active sender accounts.

---

## Mobile web client and remote access

NED includes a mobile-first web client (PWA) with a dark theme, touch gestures, one-tap archiving, and dynamic signature switching.

Want to build your own client? The HTTP API is documented in [`docs/api.md`](docs/api.md) and the running daemon serves a machine-readable OpenAPI spec at `GET /api/v1/openapi.json` (`curl --unix-socket /run/user/$UID/ned/ned.sock http://localhost/api/v1/openapi.json`).

### Remote access over Tailscale

Tailscale provides an encrypted WireGuard mesh network between your devices without exposing mail ports publicly:

1. Install Tailscale on your host machine and authenticate:
   ```bash
   tailscale up
   ```
2. Find your Tailscale IPv4 address:
   ```bash
   tailscale ip -4
   # Example: 100.82.14.95
   ```
3. Start NED listening on your Tailscale interface:
   ```bash
   ned --host 100.82.14.95 --port 8080
   ```
   Or set it permanently in `~/.config/ned/config.py`:
   ```python
   import ned.settings as settings
   settings.web_host = '100.82.14.95'
   settings.web_port = 8080
   settings.web_token = 'your-secret-token'
   ```
4. Open `http://100.82.14.95:8080` in your phone browser and install it as a home screen app:
   - **iOS Safari:** Tap Share, then **Add to Home Screen**.
   - **Android Chrome:** Tap the three dots, then **Add to Home screen** or **Install app**.

---

## The `ned-client` CLI

The `ned-client` command line tool allows scripting and querying NED directly:

```bash
# Health check
ned-client ping

# Search threads
ned-client search "tag:inbox" --limit 10

# Tag a thread
ned-client tag "thread:0000000000001234" +starred -unread

# Archive or trash a thread
ned-client archive "thread:0000000000001234"
ned-client trash "thread:0000000000001234"

# Trigger sync
ned-client sync

# Listen to live SSE invalidation stream
ned-client events
```

---

## Relationship to Dodo

Lazarus began as a personal fork of [Dodo](https://github.com/akissinger/dodo) by Aleks Kissinger. Both projects are licensed under the GNU General Public License v3. Files containing Aleks Kissinger's original code retain his copyright header, while newly created files carry the Lazarus copyright notice. See [COPYING](COPYING) for the full license text.
