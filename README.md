# Lazarus

Lazarus is a local email client that acts as a frontend for a variety of CLI
based tools. Specifically:

- [notmuch](https://notmuchmail.org/) for it's amazing indexing, tagging, and
  searching. This is the heart of Lazarus.
- [mbsync](https://isync.sourceforge.io/) or
  [offlineimap](http://www.offlineimap.org/) to sync an imap account with a
  local maildir.
- [msmtp](https://marlam.de/msmtp/) to send email
- [w3m](http://w3m.sourceforge.net/) for rendering HTML messages as plaintext ()
- [python-gnupg](https://pypi.org/project/python-gnupg/) if you want PGP
  sign/encrypt support (optional)

Lazarus began as a fork of [Dodo](https://github.com/akissinger/dodo), created
by Aleks Kissinger, and still shares some foundational code with that project.
See [Relationship to Dodo](#relationship-to-dodo) below for the full attribution
and licensing details. Lazarus as it exists today has diverged from upstream
Dodo in several ways such as a persistent split-pane thread preview, a built-in
rich-text compose editor, a 'card' view mode for email lists, mail filter rules,
signatures, an updated theme system, and more.

Lazarus is feature-complete for me personally, but has not been exhaustively
tested.

## Main goals

- SPEED - fast email reading, tagging, sorting, and composing. No mouse
  required.
- FEATURE COMPLETE - email filters, per-account signatures, built-in compose
  editor with address autocomplete and in-line images. I love nvim, but not for
  composing emails.
- HTML Native - I don't like html emails either, but so many emails are and
  sometimes w3m isn't enough.
- HACKABLE - be simple enough to customise and hack on yourself!

## Prerequisites

You'll need the CLI tools mentioned above installed and properly configured.

## Install and run

Lazarus requires Python 3.10+ and
[PyQt6](https://riverbankcomputing.com/software/pyqt/intro) 6.2+.

```
git clone this repo
cd lazarus
pipx install -e .
```

Then run it with:

```
lazarus
```

## Basic use

Before running Lazarus for the first time, set at least `email_address` and
`sent_dir` in `~/.config/lazarus/config.py` (see
[Configuration](#configuration)).

In-app, everything can be driven by keyboard shortcuts. Press `?` at any time
for the full key-binding reference.

### Layout

Lazarus has a split-pane layout. When you open an email you'll see a preview
pane of that email thread. The position of the preview pane is configurable via
`thread_pane_position` (`right`/`left`/`below`/`above`). The app starts with a
tab open on `tag:inbox`. From there:

- `j` / `k` (or ↓ / ↑) move between threads in the list; the thread under the
  cursor opens in the preview pane automatically after a short debounce
- `J` / `K` move between messages within the open thread
- `h` / `l` switch between open tabs
- `c` composes a new message; `r` / `R` reply / reply-all from the thread view;
  `C-f` forwards
- `T` opens the tag browser in a new tab
- `` ` `` triggers a manual sync; `C-r` re-applies your mail filter rules on
  demand

### Tagging and mail actions

Everything — read/unread, flags, custom tags, delete, archive — works by adding
or removing notmuch tags, plus (where relevant) physically moving the underlying
mail file:

- `u` / `f` toggle unread / flagged
- `1`–`9` toggle whatever tags you've bound to those keys (see `tag_hotkeys` in
  Configuration)
- `t t` tags the current thread; `t m` tags every currently **marked** thread
  (mark with `s`, which also advances the cursor) — most actions operate on a
  marked batch if you have one, or fall back to the current thread otherwise
- `a` archives (untags `inbox`/`unread`) — refuses if the thread has no tag
  beyond those two, so nothing vanishes into an untagged pile by accident
- `A` archives to a local-only Maildir (`archive_dir`) instead
- `d` moves the thread to Trash (soft delete — reversible); `d d` empties Trash
  for good (irreversible — this is the only genuinely destructive action in the
  app); `d u` restores a thread out of Trash

File moves for these actions happen asynchronously in the background, batched so
a flurry of `a`/`d` keypresses doesn't stall the UI, with `notmuch new` re-run
automatically once each batch lands on disk.

### Composing

<img src=images/compose.webp>

Lazarus has a built-in rich-text compose editor. It supports:

- a formatting toolbar above the body (bold / italic / underline, alignment,
  bullet & numbered lists, text colour, insert image — NerdFont icon glyphs) —
  or the Ctrl+B / Ctrl+I / Ctrl+U shortcuts
- plaintext mode — the toolbar's `[Plaintext | HTML]` toggle (far left,
  clickable, reflects state) or Shift+H: strips formatting and sends a message
  with **no HTML part** — plain `text/plain`, mutt-style; handy for replies to
  plaintext mail. In plaintext mode the formatting buttons grey out
- inline image paste and drag-and-drop
- address autocomplete drawn from your notmuch mail history
- reveal / dismiss cc and bcc fields with `M+c` and `M+b`
- add attachments via `a`
- reply/forward bodies open with **two blank lines at the top**, so there's room
  to type above the quoted/forwarded text; `<enter>` from the compose chrome
  inserts a newline **and** moves the cursor into the editor
- per-account signatures, auto-inserted based on which account you're sending
  from (`~/config/lazarus/<account>/signature[.html]`)
- switching between configured SMTP accounts with `[` / `]` (or the From
  dropdown — one item per account, addresses shown)
- PGP sign/encrypt, toggled per-message with `p` / `e` (requires `gnupg_keyid`
  configured; disabled automatically for accounts that don't have a key set)

`C-s` sends. `<escape>` exits the editor (or any header field) to the compose
panel chrome, where the compose hotkeys live; it never re-enters the editor, and
does nothing if you're already on the chrome — click the body to resume typing.

**Compose is a closed key surface.** While composing, keys never act on mail
behind you: the list/thread hotkeys (`j`/`k`/`d`/`a`/`u`/`f`, `J`/`K`, the
message-level `C-d`/`C-f`/`C-a`/`C-t`, tag hotkeys `1`–`9`, …) are all
swallowed, in the editor, the fields, and the chrome alike — so a stray chord
can't delete/archive/tag a thread you were reading. Only compose hotkeys plus
app-level keys (help `?`, sync `` ` ``, quit `C-q`, `c` new compose, `l`/`h`
tab, `I`/`U`/`F`/`T`, search bars `/`/`C-/`, rules `C-r`, theme
`M-<`/`M->`/`t h`, close `x`/`X`) remain live.

### Mail filter rules

You can define rules in `config.py` that tag and/or move mail automatically —
see [Mail filters](#mail-filters) below. They run after every sync, and can be
re-applied by hand at any time with `C-r`.

### Themes

<img src=images/catppucin.webp> <img src=images/gruvbox.webp> <img
src=images/nord.webp>

Lazarus bundles 600+ themes pre-compiled into a native, instant-loading format (<4ms).

- **Switching Themes**: Press `t h` to open the modal theme picker with autocomplete, or use `M-<` / `M->` to cycle themes live.
- **Default Theme**: Your selected theme is automatically saved to `~/.config/lazarus/lazarus.conf` as your default (the legacy `settings.theme` in `config.py` is deprecated).
- **Selection Contrast**: Selected cards use a modern tinted wash over the background, ensuring distinct semantic colors for sender, date, unread subjects, and tags remain vibrant and readable regardless of whether the theme has a subtle or high-contrast highlight.

#### Theme Inspection & Mapping Tool

Lazarus includes a standalone CLI tool in `tools/import_themes.py` with zero external dependencies (runs on standard Python 3.9+ without needing a venv).

- **Inspect a theme with truecolor ANSI terminal swatches**:
  ```bash
  python tools/import_themes.py --inspect "Gruvbox Material"
  python tools/import_themes.py --list
  ```
  This shows the 16-color ANSI palette, special terminal colors, and how each of Lazarus's 19 semantic keys is mapped with visual color swatches.

- **Global 1-to-1 Colormapping (`tools/mapping.json`)**:
  All terminal themes map cleanly onto Lazarus's 19 semantic variables via `tools/mapping.json`:
  ```json
  {
    "bg": "background",
    "fg": "foreground",
    "fg_dim": 8,
    "fg_bright": 15,
    "fg_good": 10,
    "fg_bad": 9,
    "fg_link": 12,
    "fg_button": "foreground",
    "bg_highlight": "selection-background",
    "fg_highlight": "selection-foreground",
    "fg_date": "fg_dim",
    "fg_from": "foreground",
    "fg_subject": "foreground",
    "fg_subject_unread": 14,
    "fg_subject_irrelevant": "fg_dim",
    "fg_subject_flagged": 11,
    "fg_tags": 12
  }
  ```
  Tweak any rule and recompile all 602 bundled themes in ~0.15s:
  ```bash
  python tools/import_themes.py --compile
  ```

- **Adding Custom Themes**:
  Drop any native 19-key theme JSON into `~/.config/lazarus/themes/` (you can export any theme as a template using `python tools/import_themes.py --export "Dracula"`). Custom themes appear automatically in the in-app picker.

## Configuration

Lazarus is configured via `~/.config/lazarus/config.py`, a plain Python file
that's `exec()`'d at startup. All settings live in `lazarus.settings`, each
documented with a docstring in `lazarus/settings.py` — that file is the source
of truth; nothing below is exhaustive.

The only two settings you _must_ set are your email address and where sent mail
should be stored:

```python
import lazarus
from lazarus.rules import Rule #If you want to use mail  filters

# required
lazarus.settings.email_address = 'First Last <me@domain.com>'
lazarus.settings.sent_dir = '/home/user/Mail/default/Sent'

# optional, some commonly-changed ones
lazarus.settings.file_browser_command = "fman '{dir}' /home/user/Documents"
lazarus.settings.sync_mail_command = 'mbsync -a'
lazarus.settings.mail_root = '~/Mail'
lazarus.settings.archive_dir = '~/Mail/Archive'
lazarus.settings.thread_pane_position = 'bottom'
```

`email_address` and `sent_dir` can also be dictionaries keyed by account name,
for multi-account setups (see [Multiple accounts](#multiple-accounts)).

A few settings worth knowing about beyond the basics:

| Setting                    | Default          | Purpose                                                                                                                                 |
| -------------------------- | ---------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `mail_root`                | `~/Mail`         | Root of your local Maildir tree — used to find each account's Trash/Archive/INBOX folders for the archive/trash workflow                |
| `archive_dir`              | `~/Mail/Archive` | Where `A` moves mail to locally, separate from remote archiving                                                                         |
| `thread_pane_position`     | `'right'`        | Where the persistent thread preview docks                                                                                               |
| `sync_mail_command`        | `'offlineimap'`  | Shell-command sync fallback — used **only** when `smtp_accounts = []`; otherwise sync runs `mbsync -V <account>` per configured account |
| `sync_mail_interval`       | `300`            | Seconds between automatic syncs; `-1` disables                                                                                          |
| `default_thread_list_mode` | `'conversation'` | `'conversation'` shows a flat reading order; `'thread'` shows the notmuch reply tree                                                    |
| `gnupg_keyid`              | `None`           | GPG key ID (or `{account: keyid}` dict) enabling PGP sign/encrypt in compose                                                            |
| `filter_rules`             | `[]`             | List of `dodo.rules.Rule`s — see [Mail filters](#mail-filters)                                                                          |

Any setting ending in `_command` is a shell command string;
`file_browser_command` takes a `{dir}` placeholder. If your file browser
supports choosing files and writing the result to a temp file, you can set
`file_picker_command` (with a `{tempfile}` placeholder) instead of using the
built-in file picker.

By default, remote content (images, etc.) in HTML mail is blocked and links
prompt for confirmation before opening — see `html_block_remote_requests`,
`html_confirm_open_links`, and `html_confirm_open_links_trusted_hosts` if you
want to loosen that.

### Mail filters

`filter_rules` is a list of `lazarus.rules.Rule`, each a notmuch query plus tags
to add/remove and an optional folder to move matches into:

```python
from lazarus.rules import Rule

lazarus.settings.filter_rules = [
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

Rules run automatically after every successful sync, scoped by
`filter_scope_query` (default `'tag:inbox and tag:unread'`) so a rule change
can't silently retag your entire archive the next time it runs. Tagging is
idempotent, so re-running the same rule set against already-tagged mail is
harmless. Trigger the whole set manually at any time with `C-r`.

### Key mapping

Key bindings live in `lazarus/keymap.py`, as dictionaries mapping a key string
to a `(description, function)` pair. There are five: `global_keymap`,
`search_keymap`, `thread_keymap`, `compose_keymap`, and `command_bar_keymap`.
All but `command_bar_keymap` support keychords (space-separated sequences, e.g.
`'d d'`).

Rebind a single key from `config.py`:

```python
lazarus.keymap.search_keymap['t'] = (
    'toggle todo',
    lambda p: p.toggle_thread_tag('todo'))
```

Or unmap one:

```python
del lazarus.keymap.global_keymap['Q']
```

Tag hotkeys (the `1`–`9` bindings mentioned above) are configured separately —
see `tag_hotkeys` in `settings.py`.

### Multiple accounts

If your SMTP client supports it (msmtp does), Lazarus can send from multiple
accounts. Set `smtp_accounts` to a list of account names, then switch between
them in the compose view with `[` / `]`. `email_address`, `sent_dir`, and
`gnupg_keyid` can each be given as a dict keyed by account name instead of a
single value, for per-account addresses, sent folders, and signing keys.

```python
lazarus.settings.smtp_accounts = ['default', 'work']
lazarus.settings.email_address = {
    'default': 'Me <me@personal.com>',
    'work': 'Me <me@work.com>',
}
lazarus.settings.sent_dir = {
    'default': '~/Mail/default/Sent',
    'work': '~/Mail/work/Sent',
}
```

## Mobile web interface (lazarus-web)

Lazarus includes a headless server daemon and mobile-first web interface designed for reading, tagging, and replying to email on a phone or tablet.

### Features

- **Mobile-first UI:** Nord dark theme matching desktop Lazarus, 44px+ touch targets, bottom sheets for replies and tag management, and clean iframe isolation for HTML emails.
- **Pull down to sync:** Dragging down on the thread list triggers parallel `mbsync` execution across all accounts, runs `notmuch new`, and applies mail filter rules.
- **One-tap triage:** Dedicated buttons on thread cards for `A` archive (moves mail files to `~/Mail/Archive/cur/`, strips `-inbox -unread`, and updates notmuch) and trash, complete with an undo toast.
- **Fast tagging:** Dedicated tag sheet for toggling existing tags or creating custom notmuch tags on the fly.
- **Compose and reply:** Outbound compose and replies with recipient autocomplete from your notmuch address history, multi-file attachment uploads, and account switching.
- **Signatures:** Per-account plaintext signatures are automatically pre-populated above quoted text. Switching the sending account in the dropdown swaps signatures dynamically without altering your typed text.

### Running the server

Start the web daemon directly from the CLI:

```bash
lazarus-web --port 8080
```

By default, the server binds to `127.0.0.1:8080`. You can configure host, port, and bearer token authentication in `~/.config/lazarus/config.py`:

```python
lazarus.settings.web_host = '127.0.0.1'
lazarus.settings.web_port = 8080
lazarus.settings.web_token = 'secret-token'  # optional bearer token
```

### Secure mobile access via Tailscale

Tailscale provides an encrypted WireGuard mesh network between your host machine and your mobile device, allowing secure access to `lazarus-web` without opening public router ports or exposing mail services to the internet.

1. **Install Tailscale on host:** Install Tailscale on your host computer running Lazarus and authenticate:
   ```bash
   tailscale up
   ```
2. **Install Tailscale on mobile:** Install the Tailscale app on your phone (iOS App Store or Google Play) and sign into the same account.
3. **Find your Tailscale IP:** Check your host machine's Tailscale IP address:
   ```bash
   tailscale ip -4
   # Example: 100.82.14.95
   ```
   `lazarus-web` detects and displays this IP on startup when present.
4. **Bind the server:** Launch `lazarus-web` bound to your Tailscale IP (or `0.0.0.0` with a token):
   ```bash
   lazarus-web --host 100.82.14.95 --port 8080
   ```
5. **Open on your phone:** Open your mobile browser and navigate to `http://100.82.14.95:8080` (or your machine's MagicDNS name, like `http://my-desktop:8080`).
6. **Install as a web app (PWA):**
   - **iOS Safari:** Tap the Share button, then tap **Add to Home Screen**.
   - **Android Chrome:** Tap the menu button (three dots), then tap **Add to Home screen** or **Install app**.
   This launches Lazarus in full screen without browser toolbars, providing a dedicated email experience.


## Relationship to Dodo

Lazarus started as a personal fork of [Dodo](https://github.com/akissinger/dodo)
(`dodo-mail` on PyPI), created by Aleks Kissinger, and a meaningful part of its
codebase — HTML/text rendering, key-string handling, the notmuch data models,
several UI panels — is still Aleks's original code, lightly modified. Other
parts (the persistent split-pane thread preview, the async bulk
tag/trash/archive worker, mail filter rules, the built-in rich-text compose
editor, address autocomplete, per-account signatures) are new and don't exist in
upstream Dodo.

Both projects are licensed under the GNU General Public License v3 — see
[COPYING](COPYING). Per-file copyright headers reflect actual authorship: files
that still contain Aleks Kissinger's original code keep his copyright notice,
and files written from scratch for Lazarus carry a Lazarus copyright notice
instead. If you send patches upstream or downstream, please keep that convention
intact.

Lazarus isn't currently tracking upstream Dodo commits, and there's no
expectation that changes flow in either direction — if you're looking for the
original, actively-maintained project (with a smaller, more battle-tested
codebase), Dodo is the one to use. Lazarus is maintained for personal use and
shared as-is.
