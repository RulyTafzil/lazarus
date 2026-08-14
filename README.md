# Lazarus

Lazarus is a graphical, keyboard-driven email client written in Python/PyQt6, built on the command-line email swiss-army-knife [notmuch](https://notmuchmail.org/).

Lazarus is a fork of [Dodo](https://github.com/akissinger/dodo), created by Aleks Kissinger, and still shares a substantial amount of code and design with it — see [Relationship to Dodo](#relationship-to-dodo) below for the full attribution and licensing details. This README describes Lazarus as it exists today, which has diverged from upstream Dodo in several ways such as a persistent split-pane thread preview, a built-in rich-text compose editor, mail filter rules, signatures, and more.

As an email client, Lazarus is feature-complete for daily use but not exhaustively tested. Since it's built on notmuch, all operations work through tags and file moves rather than deleting anything outright, so you're very unlikely to lose mail to a bug — but as with any hackable tool you maintain yourself, use your own judgment.


## Main goals

* efficient, keyboard-oriented mail reading, sorting, and composing — no mouse required
* a persistent thread preview pane so reading mail doesn't mean juggling tabs
* a mostly text-based email experience by default, with HTML support a few keystrokes away
* offload as much work as possible onto existing, excellent command-line tools (notmuch, mbsync/offlineimap, msmtp, w3m — UNIX-philosophy style)
* be simple enough to customise and hack on yourself


## Prerequisites

Lazarus depends on a variety of tools to handle it's functions. 

* something to sync IMAP mail to a local Maildir — [mbsync](https://isync.sourceforge.io/) or [offlineimap](http://www.offlineimap.org/) both work; `mbsync` is what the default settings assume
* a sendmail-compatible SMTP client to send mail — [msmtp](https://marlam.de/msmtp/) is the default
* [notmuch](https://notmuchmail.org/) for email indexing, searching, and tagging
* [w3m](http://w3m.sourceforge.net/) for rendering HTML messages as plaintext
* [python-gnupg](https://pypi.org/project/python-gnupg/) if you want PGP sign/encrypt support (optional)

All of the above are standard packages on Linux/macOS package managers.


## Install and run

Lazarus requires Python 3.10+ and [PyQt6](https://riverbankcomputing.com/software/pyqt/intro) 6.2+.

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

Before running Lazarus for the first time, set at least `email_address` and `sent_dir` in `~/.config/lazarus/config.py` (see [Configuration](#configuration)).

Nearly everything is driven by keyboard shortcuts. Press `?` at any time for the full key-binding reference.

### Layout

Lazarus opens with a split-pane layout: a tabbed list view (search results, compose panels, the tag browser) on one side, and a **persistent thread preview pane** on the other — configurable via `thread_pane_position` (`right`/`left`/`below`/`above`).
The app starts with a tab open on `tag:inbox`. From there:

* `j` / `k` (or ↓ / ↑) move between threads in the list; the thread under the cursor opens in the preview pane automatically after a short debounce
* `J` / `K` move between messages within the open thread
* `h` / `l` switch between open tabs
* `c` composes a new message; `r` / `R` reply / reply-all from the thread view; `C-f` forwards
* `T` opens the tag browser in a new tab
* `` ` `` triggers a manual sync; `C-r` re-applies your mail filter rules on demand

### Tagging and mail actions

Everything — read/unread, flags, custom tags, delete, archive — works by adding or removing notmuch tags, plus (where relevant) physically moving the underlying mail file:

* `u` / `f` toggle unread / flagged
* `1`–`9` toggle whatever tags you've bound to those keys (see `tag_hotkeys` in Configuration)
* `t t` tags the current thread; `t m` tags every currently **marked** thread (mark with `s`, which also advances the cursor) — most actions operate on a marked batch if you have one, or fall back to the current thread otherwise
* `a` archives (untags `inbox`/`unread`) — refuses if the thread has no tag beyond those two, so nothing vanishes into an untagged pile by accident
* `A` archives to a local-only Maildir (`archive_dir`) instead
* `d` moves the thread to Trash (soft delete — reversible); `d d` empties Trash for good (irreversible — this is the only genuinely destructive action in the app); `d u` restores a thread out of Trash

File moves for these actions happen asynchronously in the background, batched so a flurry of `a`/`d` keypresses doesn't stall the UI, with `notmuch new` re-run automatically once each batch lands on disk.

### Composing

Lazarus has a built-in rich-text compose editor (no external `$EDITOR` round-trip required). It supports:

* inline image paste and drag-and-drop
* address autocomplete drawn from your notmuch mail history
* attachments via `a`
* per-account signatures, auto-inserted based on which account you're sending from (`~/.config/dodo/<account>/signature[.html]`)
* switching between configured SMTP accounts with `[` / `]`
* PGP sign/encrypt, toggled per-message with `p` / `e` (requires `gnupg_keyid` configured; disabled automatically for accounts that don't have a key set)

`C-s` sends. `<escape>` toggles focus between the editor and the rest of the compose panel's chrome (subject, to/cc/from fields, etc).

### Mail filter rules

You can define rules in `config.py` that tag and/or move mail automatically — see [Mail filters](#mail-filters) below. They run after every sync, and can be re-applied by hand at any time with `C-r`.

### Themes

Built-in themes: `nord`, `solarized_dark`, `solarized_light`, `catppuccin_macchiato`, and several Gruvbox variants (`gruvbox_light`, `gruvbox_light_hard`, `gruvbox_light_soft`, `gruvbox_dark`, `gruvbox_dark_hard`, `gruvbox_dark_soft`). A theme is just a dict of color names to hex codes, so rolling your own is straightforward — see `dodo/themes.py`.


## Configuration

Lazarus is configured via `~/.config/lazarus/config.py`, a plain Python file that's `exec()`'d at startup. All settings live in `lazarus.settings`, each documented with a docstring in `lazarus/settings.py` — that file is the source of truth; nothing below is exhaustive.

The only two settings you *must* set are your email address and where sent mail should be stored:

```python
import lazarus
from lazarus.rules import Rule #If you want to use mail  filters

# required
lazarus.settings.email_address = 'First Last <me@domain.com>'
lazarus.settings.sent_dir = '/home/user/Mail/default/Sent'

# optional, some commonly-changed ones
lazarus.settings.theme = lazarus.themes.nord
lazarus.settings.file_browser_command = "fman '{dir}' /home/user/Documents"
lazarus.settings.sync_mail_command = 'mbsync -a'
lazarus.settings.mail_root = '~/Mail'
lazarus.settings.archive_dir = '~/Mail/Archive'
lazarus.settings.thread_pane_position = 'right'
```

`email_address` and `sent_dir` can also be dictionaries keyed by account name, for multi-account setups (see [Multiple accounts](#multiple-accounts)).

A few settings worth knowing about beyond the basics:

| Setting | Default | Purpose |
|---|---|---|
| `mail_root` | `~/Mail` | Root of your local Maildir tree — used to find each account's Trash/Archive/INBOX folders for the archive/trash workflow |
| `archive_dir` | `~/Mail/Archive` | Where `A` moves mail to locally, separate from remote archiving |
| `thread_pane_position` | `'right'` | Where the persistent thread preview docks |
| `sync_mail_command` | `'offlineimap'` | Change to `'mbsync -a'` (or similar) if that's what you use |
| `sync_mail_interval` | `300` | Seconds between automatic syncs; `-1` disables |
| `default_thread_list_mode` | `'conversation'` | `'conversation'` shows a flat reading order; `'thread'` shows the notmuch reply tree |
| `gnupg_keyid` | `None` | GPG key ID (or `{account: keyid}` dict) enabling PGP sign/encrypt in compose |
| `filter_rules` | `[]` | List of `dodo.rules.Rule`s — see [Mail filters](#mail-filters) |

Any setting ending in `_command` is a shell command string; `file_browser_command` takes a `{dir}` placeholder. If your file browser supports choosing files and writing the result to a temp file, you can set `file_picker_command` (with a `{tempfile}` placeholder) instead of using the built-in file picker.

By default, remote content (images, etc.) in HTML mail is blocked and links prompt for confirmation before opening — see `html_block_remote_requests`, `html_confirm_open_links`, and `html_confirm_open_links_trusted_hosts` if you want to loosen that.

### Mail filters

`filter_rules` is a list of `lazarus.rules.Rule`, each a notmuch query plus tags to add/remove and an optional folder to move matches into:

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

Rules run automatically after every successful sync, scoped by `filter_scope_query` (default `'tag:inbox and tag:unread'`) so a rule change can't silently retag your entire archive the next time it runs. Tagging is idempotent, so re-running the same rule set against already-tagged mail is harmless. Trigger the whole set manually at any time with `C-r`.

### Key mapping

Key bindings live in `lazarus/keymap.py`, as dictionaries mapping a key string to a `(description, function)` pair. There are five: `global_keymap`, `search_keymap`, `thread_keymap`, `compose_keymap`, and `command_bar_keymap`. All but `command_bar_keymap` support keychords (space-separated sequences, e.g. `'d d'`).

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

Tag hotkeys (the `1`–`9` bindings mentioned above) are configured separately — see `tag_hotkeys` in `settings.py`.

### Multiple accounts

If your SMTP client supports it (msmtp does), Lazarus can send from multiple accounts. Set `smtp_accounts` to a list of account names, then switch between them in the compose view with `[` / `]`. `email_address`, `sent_dir`, and `gnupg_keyid` can each be given as a dict keyed by account name instead of a single value, for per-account addresses, sent folders, and signing keys.

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


## Relationship to Dodo

Lazarus started as a personal fork of [Dodo](https://github.com/akissinger/dodo) (`dodo-mail` on PyPI), created by Aleks Kissinger, and a meaningful part of its codebase — HTML/text rendering, key-string handling, the notmuch data models, several UI panels — is still Aleks's original code, lightly modified. Other parts (the persistent split-pane thread preview, the async bulk tag/trash/archive worker, mail filter rules, the built-in rich-text compose editor, address autocomplete, per-account signatures) are new and don't exist in upstream Dodo.

Both projects are licensed under the GNU General Public License v3 — see [COPYING](COPYING). Per-file copyright headers reflect actual authorship: files that still contain Aleks Kissinger's original code keep his copyright notice, and files written from scratch for Lazarus carry a Lazarus copyright notice instead. If you send patches upstream or downstream, please keep that convention intact.

Lazarus isn't currently tracking upstream Dodo commits, and there's no expectation that changes flow in either direction — if you're looking for the original, actively-maintained project (with a smaller, more battle-tested codebase), Dodo is the one to use. Lazarus is maintained for personal use and shared as-is.
