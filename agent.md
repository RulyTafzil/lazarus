# lazarus — Lazarus Mail Client

## Identity
- **Name**: Lazarus — a fork of [Dodo](https://github.com/akissinger/dodo) by Aleks Kissinger
- **Package**: `lazarus-mail` (`0.3`)
- **Original Author**: Aleks Kissinger <aleks0@gmail.com> (Dodo); **Maintainer**: Ruly Tafzil (fork)
- **License**: GPLv3 (`COPYING`)
- **Local dir**: `~/Projects/lazarus`
- **Forgejo**: `ssh://forgejo@forge.rulytafzil.com:2222/Home/lazarus.git` (branch `main`)
- **Upstream**: `https://github.com/akissinger/dodo.git` (remote `upstream`, not tracked)
- **CLI**: `lazarus` (desktop GUI client; `lazarus --install-desktop` installs desktop entry + icons); `ned` (Notmuch Email Daemon); `ned-client` (CLI client for NED)
- **Entry point**: `lazarus.app:main` (`lazarus/__main__.py` -> `app.main()`); `lazarus.ned.main:main` (`ned`)
- **Config**: Cascading search: `~/.config/ned/config.py` first, falling back to `~/.config/lazarus/config.py` (loaded via `lazarus.config.load_config()`)
- **State**: `QSettings('lazarus','lazarus')` for desktop geometry, splitter, open searches; NED state in `~/.local/share/lazarus/ned/`
- **Install**: `pipx install -e ~/Projects/lazarus` (editable)

## What It Is
A keyboard-driven email system comprising the Notmuch Email Daemon (NED) and the
Lazarus desktop client. NED acts as the authoritative daemon managing Notmuch
indexing, Maildir synchronization, mutation locks, and Server-Sent Events (SSE).
Lazarus desktop is a pure PyQt6 client that reacts to daemon invalidations over a
Unix domain socket, featuring vim-like keychords, split-pane layout with persistent
thread preview, and a built-in rich-text compose editor. All mutations use Notmuch
tags and Maildir moves. No email is ever deleted outright.

Diverged from upstream Dodo: persistent split-pane thread preview, async bulk
tag/trash/archive worker, mail filter rules, built-in `RichTextEditor` (with
plaintext mode), address autocomplete, per-account signatures (plain + HTML),
parallel mbsync, 600+ terminal-style theme library (`themes.REGISTRY`) with
live switching, low-poly watermark tab background, hicolor icons.

## Tech Stack

| Layer | Tech | Notes |
|-------|------|-------|
| Language | Python 3.10+ (`python_requires=">=3.10"`) | `from __future__ import annotations` throughout |
| GUI | PyQt6 `>=6.2`, PyQt6-WebEngine `>=6.2` | `QApplication`/`QMainWindow`/`QSplitter`/`QTreeView`/`QWebEngineView`/`QTabWidget`/`QStackedWidget` |
| Type checking | mypy `disallow_untyped_defs = True` (`mypy.ini`) | must stay at 0 errors |
| HTML | `bleach >=5.0`, `w3m` | `w3m` renders HTML→plaintext; bleach `Linker` linkifies URLs |
| Email index | `notmuch` CLI | Wrapped by `lazarus/notmuch.py` |
| IMAP sync | `mbsync -V <acct>` per account (parallel) | `sync_mail_command` shell fallback used **only** when `smtp_accounts` is empty |
| SMTP send | `msmtp` | `msmtp -a "{account}" -t`; per-account via `send_mail_command` dict |
| PGP | `python-gnupg` (optional) | `lazarus/pgp_util.py`; `gnupg_home`/`gnupg_keyid` settings |
| Build | `setuptools >=42` | `setup.py` + `pyproject.toml`; icons via `package_data` + `--install-desktop` (no `data_files`) |
| Logging | stdlib `logging` | Level/file from `settings.log_level`/`log_file`; status bar mirror |

## Project Layout
```
~/Projects/lazarus/
├── agent.md                # this file
├── lazarus/                # Python package (11.3k LOC)
│   ├── __init__.py         # re-exports app/themes/settings/keymap/util
│   ├── __main__.py         # app.main() entry
│   ├── app.py              # Dodo(QApplication) bootstrap (~370L) — config+logging, signals, HelpWindow, sync timer, Chromium warm-up; no panel imports at runtime (all orchestration on AppController)
│   ├── client.py           # Desktop client singleton (get_client, is_ned_active, ensure_daemon)
│   ├── controller.py       # AppController(QObject) + SyncMailThread + _NedEventBridge — panel registry, sync engine, SSE invalidation
│   ├── mainwindow.py       # MainWindow(QMainWindow) — splitter, tabs, preview, status bar
│   ├── panel.py            # Panel(QWidget) base — keychords, dirty flag, debounce
│   ├── search.py           # SearchPanel + SearchModel + render_thread_cell()
│   ├── thread.py           # ThreadPanel — double-buffered web view + message list
│   ├── thread_model.py     # ThreadModel + ThreadItem (conversation/thread modes)
│   ├── compose.py          # ComposePanel — reply/forward, account switch, sig, PGP
│   ├── compose_model.py    # Qt-free seeds + sig_edit() placement logic
│   ├── editor.py           # RichTextEditor(QTextEdit) — inline images, paste/drag-drop, formatting toolbar, plaintext mode
│   ├── compose_threads.py  # SendmailThread (msmtp + sent save)
│   ├── mime_builder.py     # ComposeData dataclass + build_message() (multipart/related)
│   ├── actions.py          # MarkableActionsMixin + _BulkMoveWorker + move/expunge helpers
│   ├── rules.py            # Rule dataclass + apply_rules() (filter engine)
│   ├── tag.py              # TagPanel + TagModel
│   ├── commandbar.py       # CommandBar(QPlainTextEdit) — centered modal overlay, grow-to-content, bg tag loader, history, search/tag/theme modes
│   ├── keymap.py           # global_keymap (consolidated) + tag/compose/command_bar maps
│   ├── address_completer.py# AddressCompleter + _AddressLoader (notmuch address bg thread)
│   ├── webengine.py        # MessagePage/Handler, EmbeddedImageHandler, RemoteBlocker
│   ├── themes.py           # Hand-written palettes + terminal-theme pack import + apply_theme()
│   ├── config.py           # ConfigError + load_config() + _validate_settings() (exec + validation gate)
│   ├── settings.py         # All defaults with docstrings (validated by config.py after exec)
│   ├── signature.py        # Per-account signature loader (signature / signature.html)
│   ├── pgp_util.py         # PGP/MIME sign/encrypt via python-gnupg
│   ├── notmuch.py          # Thin CLI wrapper — run/count/count_batch/tags/search_files/search_json/show_part/tag/new
│   ├── util.py             # Compat shim re-exporting split helpers + email/account helpers
│   ├── html_utils.py       # linkify, colorize_text, w3m_html2text, html_to_plain
│   ├── mail_utils.py       # message_parts, body_text/html, quote, write_attachments
│   ├── keys.py             # key_string + basic_keytab/keytab
│   ├── style.py            # Memoised cell_font/theme_color; NerdFont family + glyph_image()
│   ├── protocols.py        # PanelApp/ThreadList/ThreadView protocols + method sets
│   ├── helpwindow.py       # HelpWindow — keybinding HTML
│   ├── core/               # Headless domain engine (zero Qt dependencies)
│   │   ├── actions.py      # Pure file move planners, _BulkMoveWorker(threading.Thread), expunge/restore
│   │   ├── service.py      # Headless domain services: queries, thread assembly, tags, contacts, send
│   │   └── sync.py         # Pure parallel mbsync, notmuch new, rules runner (run_sync, SyncResult)
│   ├── ned/                # Notmuch Email Daemon (NED)
│   │   ├── concurrency.py  # MutationLock: serialized mutation write queue
│   │   ├── events.py       # EventBroadcaster: SSE invalidation broadcaster
│   │   ├── handler.py      # NedRequestHandler: /api/v1/ routes, legacy aliases, SSE, static
│   │   ├── daemon.py       # NedDaemon: Unix domain socket + TCP listeners + sync scheduler
│   │   ├── client.py       # NedClient: zero-dependency Unix socket & HTTP client library
│   │   ├── main.py         # ned CLI entry point
│   │   └── static/         # Mobile PWA web assets served directly by NED
│   ├── ned_client.py       # Top-level standalone entry point for NedClient
│   └── theme_packs/
│       ├── builtin.json          # 602 pre-compiled native 19-key themes (~4ms load)
│       └── raw_terminal_themes.json  # Raw terminal palette sources
├── tools/
│   ├── import_themes.py          # Zero-dep CLI for theme inspection, truecolor swatches, compilation, and export
│   └── mapping.json              # Clean 1-to-1 mapping from terminal/ANSI properties to Lazarus's 19 semantic keys
├── tests/                  # 412 tests (pytest, offscreen Qt, notmuch stubbed)
├── images/                 # README screenshots (compose.webp, catppucin/gruvbox/nord.webp)
├── docs/                   # Sphinx (Makefile, make.bat, source/)
├── README.md
├── COPYING                 # GPLv3
├── setup.py                # lazarus-mail 0.3; package_data icons + theme_packs
├── pyproject.toml          # setuptools build-system
├── mypy.ini / MANIFEST.in / .mailmap / .readthedocs.yaml / .gitignore
└── Lazarus.png             # 1024px source icon
```

## Architecture

### Split-Pane Layout
```
┌──────────────────────────────────────────────────────────┐
│ MainWindow (QSplitter — orientation from thread_pane_position) │
│ ┌──────────────────────┐ ┌─────────────────────────────┐ │
│ │ QTabWidget           │ │ QStackedWidget (preview)    │ │
│ │  ├─ SearchPanel      │ │  ├─ ThreadPanel             │ │
│ │  ├─ ComposePanel     │ │  └─ QLabel placeholder      │ │
│ │  └─ TagPanel         │ │     "Select a thread…"      │ │
│ └──────────────────────┘ └─────────────────────────────┘ │
│ CommandBar (centered modal overlay, grow-to-content) / StatusBar │
└──────────────────────────────────────────────────────────┘
```
- Preview starts collapsed (list full width); the last open divider is
  restored on `show_thread()` (`_load_open_splitter_state`, ~50/50 default).
- `Enter` on a thread → `SearchPanel.open_current_thread()` → `MainWindow.show_thread()` (no thread tabs).
- `Escape` in preview → `MainWindow.focus_list()`; `C-Enter` → `clear_thread()`.
- `h`/`l` switch `QTabWidget` tabs; `j`/`k` move thread cursor; `J`/`K` move message cursor in preview.
- `j`/`k` auto-opens the selected thread after a 150 ms debounce (only if the preview is visible).
- `QSettings` persists `main_window_geometry`, splitter state, `open_searches` (restored at startup).

### Class Hierarchy
```
QApplication
 └── Dodo (app.py) — bootstrap-only (~370L): config+logging, signal plumbing, lazy
     HelpWindow on first '?' press, sync timer, deferred Chromium warm-up via
     QTimer.singleShot(0), theme registry build + initial resolve. Cold launch
     dropped from ~760ms to ~110ms (~7x speedup). Keeps only show_help / sync_mail /
     _cleanup_sync / _restore_open_searches (Qt lifecycle). Panels receive AppController
     — the SOLE PanelApp implementer.
      └── controller.AppController (QObject) — panel registry + SyncMailThread; the single
          PanelApp interface the global keymap and panels dispatch to. app.py imports no
          panel modules (cycle broken at runtime); actions._BulkMoveWorker wired via
          set_batch_done_listener().

QMainWindow
 └── MainWindow (mainwindow.py)
      ├── QSplitter ──┬── QTabWidget (list tabs: SearchPanel/ComposePanel/TagPanel)
      │               └── QStackedWidget (thread preview + placeholder QLabel)
      ├── CommandBar (commandbar.py — QPlainTextEdit, modal overlay)
      ├── StatusBar (QLabel, auto-hide timer, kind info/error)
      └── Window icon (QIcon.fromTheme('lazarus') → bundled 1024 PNG fallback)

Panel(QWidget) — base for all views (panel.py, has_refreshed signal)
 ├── SearchPanel (search.py)    — SearchModel + QTreeView (5 cols); MarkableActionsMixin
 ├── ThreadPanel (thread.py)    — ThreadModel + QTreeView + double-buffered QWebEngineView
 ├── ComposePanel (compose.py)  — RichTextEditor + headers + attachments + PGP + account switch + sig
 └── TagPanel (tag.py)          — TagModel + QTreeView (tag browser, batched counts)

Models (QAbstractItemModel):
 ├── SearchModel — wraps notmuch search --format=json (thread dicts)
 ├── ThreadModel — wraps notmuch show --format=json, ThreadItem tree, conversation/thread modes
 └── TagModel    — wraps notmuch tags + count --batch

Helpers:
 ├── RichTextEditor(QTextEdit) — inline image temp dir, body_html/text, collect_inline_images,
 │                               formatting_toolbar(), toggle_plain() (plaintext mode)
 ├── AddressCompleter(QCompleter) — notmuch address preload thread, MatchContains
 ├── MessagePage/Handler, EmbeddedImageHandler, RemoteBlockingUrlRequestInterceptor (webengine.py)
 ├── SendmailThread (compose_threads.py — QThread)
 └── _BulkMoveWorker(QThread) + MarkableActionsMixin (actions.py)
```

### Data Flow
```
Search:   notmuch search --format=json → SearchModel → QTreeView (render_thread_cell, 5 cols)
Thread:   notmuch show --format=json   → ThreadModel (ThreadItem tree) → QTreeView + QWebEngineView
          (MessagePage, double-buffered, _SwapGuard arbitrates stale loads)
Compose:  fields (To/Cc/Bcc/Subject/From) + RichTextEditor + attachments + inline images
            → ComposeData (mime_builder) → build_message() → multipart/related MIME
            → SendmailThread → msmtp → mailbox save to sent_dir → status bar
          Signature: seeds provide the quote (ComposeSeed.quoted_tail); _insert_signature
            places the sig block structurally via compose_model.sig_edit (no markers);
            HTML sig file used in rich mode, plain block otherwise.
Sync:     SyncMailThread (parallel mbsync -V per account + notmuch new)
            → controller.refresh_panels() → rules.apply_rules(filter_rules, filter_scope_query)
            (auto after sync; C-r manual) → moves enqueued to _BulkMoveWorker
Tag/Move: MarkableActionsMixin.tag/archive/delete → notmuch.tag() → collect_files()
            → _resolve_stale_path() → _BulkMoveWorker.enqueue([(src,dst)]) (serial QThread)
            → notmuch new (--no-hooks) → batch_done → refresh_panels (listener-registered)
Delete:   d → +trash tag + move files to [Gmail]/Trash or [account]/Trash; d d → expunge (irreversible)
Archive:  a → -inbox -unread; A → archive_to_local → move to ~/Mail/Archive
```

## Module Map

| File | Lines | Purpose |
|------|-------|---------|
| `themes.py` | 1242 | Hand-written palettes + pre-compiled native theme loader (fast O(1) path for 19 `THEME_KEYS`, ~4ms load) + legacy terminal pack heuristic fallback, `load_theme_pack`, `build_registry` (hand-written > bundled pack > user packs > overrides), `REGISTRY`, `apply_theme()` global QSS builder, live switching (`set_theme`/`cycle_theme`/`ordered_names`, QSettings `last_theme_name`); `settings.theme` in config.py is deprecated in favor of in-app choice |
| `compose.py` | 804 | `ComposePanel` — compose/reply/forward; labeled field rows (hidden Cc/Bcc via M-c/M-b), From = account dropdown (`_set_account` shared with `[`/`]`), PGP/send status, `_insert_signature` (structural placement via `sig_edit`, HTML sigs in rich mode), `SendmailThread` wiring, `RichTextEditor` integration |
| `controller.py` | 737 | `AppController(QObject)` — panel registry, `open_*`, `delegate_*`, `refresh_panels`, `refresh_tab_titles` (batched `count --batch`), `sync_mail` + `SyncMailThread`, theme switching, `close_panel` (deleteLater + in-flight-send guard). Sole `PanelApp` implementer |
| `thread.py` | 672 | `ThreadPanel` — double-buffered `QWebEngineView` + `_SwapGuard` (newest-load-wins), message list, `toggle_html`/`toggle_remote_content`/`toggle_list_mode`, `open_attachments`, `scroll_message` |
| `mainwindow.py` | 653 | `MainWindow` — splitter + tabs + preview, `WatermarkTabWidget` (low-poly mesh + watermark, `invalidate_mesh()` on theme switch), CommandBar/StatusBar overlay, splitter open-state persistence (`_load_open_splitter_state`), geometry/open-searches persistence |
| `editor.py` | 617 | `RichTextEditor(QTextEdit)` — temp dir for inline images, `insert_image_from_file/data`, `body_html/text`, `collect_inline_images` (file:// → cid:), paste/drag-drop, `formatting_toolbar()` (NerdFont glyphs, synced to cursor format), `toggle_plain()` plaintext mode |
| `search.py` | 550 | `SearchPanel` + `SearchModel` + `render_thread_cell()` + `CardDelegate`; in-place thread refresh with fine-grained `beginRemoveRows`/`endRemoveRows` when threads leave query; modern tinted selection wash (25% blend of `bg_highlight` over `bg` when high-contrast) to keep distinct text colors for From, Subject, Date, Tags; `search_list_mode` 'list'/'card' (config-only); `title()` pure (counts via controller batch) |
| `thread_model.py` | 411 | `ThreadModel(QAbstractItemModel)` + `ThreadItem` — tree (O(1) parent via `row_in_parent`), conversation/thread modes, message tagging |
| `app.py` | 370 | `Dodo(QApplication)` bootstrap — validated config, logging, signals (Ctrl-C pipe), lazy `HelpWindow`, sync timer, deferred Chromium warm-up (`QTimer.singleShot(0)`), theme registry build + initial resolve; `main()`/`install_desktop()` |
| `tools/import_themes.py` | 362 | Zero-dependency CLI tool — inspect theme colors with truecolor ANSI swatches, compile raw packs to native format, 1-to-1 colormapping via `tools/mapping.json`, export standalone themes |
| `panel.py` | 364 | `Panel(QWidget)` base — keychord handling (prefix timer, parented QTimers), dirty tracking, 150 ms debounce, `has_refreshed`, `HeaderInsetTreeView` |
| `commandbar.py` | 363 | `CommandBar(QPlainTextEdit)` — modal overlay (dim + click-away), grow-to-content, tag + theme-name autocomplete, per-mode history; process-wide `_TagStore` owns the bg tag loader (outlives bars) |
| `address_completer.py` | 250 | `AddressCompleter(QCompleter)` + singleton `_AddressLoader(QThread)` — `notmuch address` preload, MatchContains, min 2 chars |
| `webengine.py` | 247 | `MessagePage`, `MessageHandler`, `EmbeddedImageHandler` (tolerates stale/missing files), `RemoteBlockingUrlRequestInterceptor`, `LOCAL_PROTOCOLS=[cid,message]` |
| `compose_model.py` | 236 | Qt-free: `ComposeSeed` + `build_reply/forward/mailto` (quote-only bodies + `quoted_tail`), `sig_edit()` pure placement, `sig_block_text`, account helpers |
| `tag.py` | 218 | `TagPanel` + `TagModel` — tag browser with unread/total counts (batched) |
| `mime_builder.py` | 211 | `ComposeData` dataclass + `build_message()` — plain/HTML/multipart/related, attachments, inline images |
| `keymap.py` | 194 | Consolidated `global_keymap` (C-q quit, `t h` theme bar, `M-<`/`M->` theme cycle) + empty `search_keymap`/`thread_keymap` (override hooks), `tag_keymap`, `compose_keymap`, `command_bar_keymap`; `1-9` tag hotkeys |
| `keys.py` | 187 | `key_string` + `basic_keytab`/`keytab` |
| `mail_utils.py` | 186 | Message-part helpers — `message_parts`, `body_text/html`, `quote_body_text` (tolerates missing Date/From), `write_attachments` |
| `util.py` | 184 | Compat shim re-exporting split helpers + owned email/account helpers, `make_message_css`, `sort_tags` |
| `config.py` | 179 | `ConfigError` + `load_config()` — exec config.py with file:lineno + validation |
| `notmuch.py` | 168 | Wrapper — `run`, `count`, `count_batch(--batch)`, `tags`, `search_files`, `search_json`, `show_part` (binary), `tag`, `new` |
| `style.py` | 167 | Memoised `cell_font()`/`theme_color(_or)()`; `nerd_font_family()` + `glyph_image()` (glyph → temp PNG for QSS); `disabled_foreground()` |
| `pgp_util.py` | 165 | PGP/MIME sign/encrypt via python-gnupg |
| `protocols.py` | 158 | `PanelApp`/`ThreadList`/`ThreadView` structural protocols + `LIST_METHODS`/`THREAD_METHODS` fail-fast sets |
| `compose_threads.py` | 143 | `SendmailThread` — build MIME, In-Reply-To/References, PGP, msmtp, sent save, `send_success`/`send_error` |
| `rules.py` | 137 | `Rule(query, tag_add, tag_remove, move_to)` + `apply_rules()` — scoped by `filter_scope_query`, moves via `move_specific_files` |
| `helpwindow.py` | 133 | `HelpWindow` — keybinding HTML |
| `html_utils.py` | 116 | `linkify`, `colorize_text`, `w3m_html2text`, `html_to_plain` (sig search keys) |
| `signature.py` | 90 | `config_dir(account)` + per-account `signature`/`signature.html` loader (QStandardPaths) |
| `core/service.py` | 370 | Headless domain services: queries, thread flattening, tag modifications, msmtp delivery |
| `__init__.py` / `__main__.py` | 23/22 | Re-exports; entry point |

## Icons & Desktop Integration

### Window Icon
`QIcon.fromTheme('lazarus')` first (XDG `~/.local/share/icons/hicolor/`), fallback to bundled `lazarus/icons/hicolor/1024x1024/apps/lazarus.png` shipped as `package_data`. No SVG — all sizes from PNG set.

### Desktop Entry (`app.py:install_desktop`)
`lazarus --install-desktop` copies the bundled PNGs (package_data) to `~/.local/share/icons/hicolor/` and writes the embedded `_DESKTOP_ENTRY` to `~/.local/share/applications/lazarus.desktop`, then runs `update-desktop-database`. This is the **single** install path — there is no `data_files`/`share/` mechanism anymore (data_files landed inside pipx venvs where no desktop looks; removed 2026-08).

## Key Bindings

All `global_keymap` keys delegate via `AppController.delegate_to_list` / `delegate_to_thread` / direct controller methods, so they work regardless of focus. `search_keymap`/`thread_keymap` are empty dicts kept for `config.py` overrides.

### Navigation (global)
| Key | Action |
|-----|--------|
| `j` / `k` / `↓` / `↑` | Next/previous thread |
| `<tab>` / `S-<tab>` | Next/previous unread thread |
| `J` / `K` | Next/previous message in thread preview |
| `g g` / `G` | First/last thread |
| `M-j` / `M-k` | Down/up 20 threads |
| `<pageup>` / `<pagedown>` | Page up/down (list) |
| `<space>` / `-` | Page down/up (message) |
| `<enter>` | Open thread in preview |
| `<escape>` | Focus list |
| `C-<enter>` | Close thread preview |
| `h` / `l` | Previous/next tab |
| `x` / `X` | Close panel / close all |

### Actions (global → list)
| Key | Action |
|-----|--------|
| `s` | Mark thread and advance |
| `a` | Archive current/marked (`-inbox -unread`) |
| `d` | Delete to trash (current/marked → `+trash` + file move) |
| `d d` | Empty trash (irreversible) |
| `d u` | Restore from trash |
| `A` | Archive to local Maildir (`~/Mail/Archive`) |
| `u` / `f` | Toggle `unread`/`flagged` |
| `t t` | Tag current thread |
| `t m` | Tag all marked threads |
| `t h` | Theme picker (command bar in `theme` mode, `theme:` prefilled, autocomplete) |
| `1`–`9` | Toggle `tag_hotkeys` tag |

### Global / Views
| Key | Action |
|-----|--------|
| `?` | Help window |
| `C-q` | Quit (`prompt_quit`) |
| `` ` `` | Manual sync |
| `c` | Compose |
| `I` / `U` / `F` | Show inbox / unread / flagged |
| `T` | Tag browser |
| `/` | Search bar |
| `C-/` | Edit current tab query in-place |
| `C-r` | Apply filter rules |
| `M-<` / `M->` | Previous/next theme (Alt — `C-<` collides with the `C-<enter>` chord prefix) |

### Thread View (global → thread preview)
| Key | Action |
|-----|--------|
| `H` | Toggle HTML/plaintext |
| `i` | Toggle remote images |
| `M` | Toggle list mode (conversation ↔ tree) |
| `r` / `R` | Reply all / reply — focused context: preview's current message, or the list's selected thread |
| `C-y` | Forward — same focused-context rule |
| `O` | Open attachments |

### Thread View — message-level actions (Ctrl-variants act on the selected
message in the preview; the plain keys act on the whole thread)
| Key | Action |
|-----|--------|
| `C-u` / `C-f` | Toggle `unread` / `flagged` on the current message |
| `C-a` / `C-A` | Archive / archive-to-local the current message |
| `C-d` | Move current message to Trash |
| `C-t` | Tag current message (modal, `+` prefilled) |

### Tag Panel (`tag_keymap`)
`j`/`k`/`↓`/`↑`, `g g`/`G`, `C-d` down 20 / `C-u` up 20, `<enter>` (search tag).

### Compose (`compose_keymap`)
| Key | Action |
|-----|--------|
| `<escape>` | Exit the editor (or a header field) to the compose chrome — one-directional, never re-enters the editor; no-op from the chrome (click the body to resume typing) |
| `<enter>` | Insert newline; from the chrome, also moves focus into the editor (typing continues there) |
| `H` | Toggle plaintext compose (Shift+H in chrome; toolbar `[Plaintext \| HTML]` toggle too). Plain mode: formatting stripped, outgoing message has no HTML part, formatting buttons disabled |
| `C-s` | Send (`SendmailThread`, disables UI while sending) — also works while the editor is focused |
| `M-c` / `M-b` | Toggle hidden Cc / Bcc row (content in a hidden row is disregarded on send but remembered) |
| `a` | Attach file |
| `[` / `]` | Previous/next SMTP account (cycles From dropdown, reloads + re-places sig) |
| `p` / `e` | Toggle PGP sign/encrypt |

**Compose is a closed key surface**: keys may only act on the visible
compose panel or app-level things (help, sync, quit, new compose, tab
switch, search bars, rules, theme). Everything that would delegate to the
hidden thread list / thread preview (`j`/`k`/`d`/`a`/`u`/`f`, `J`/`K`,
message-level `C-d`/`C-f`/`C-a`/`C-t`, tag hotkeys `1`–`9`) is swallowed in
every focus state — via `ComposePanel._allow_global_key` gating the global
fallthrough in `Panel.keyPressEvent` against `keymap.COMPOSE_ALLOWED_GLOBALS`
(both the single-key path and the keychord-timeout path).

### Command Bar (`command_bar_keymap`)
`<enter>` accept, `<escape>` close, `<down>`/`<up>` history.

## Configuration

Config is a Python file at `~/.config/lazarus/config.py` located via `QStandardPaths` and `exec()`'d at startup. All settings live in `lazarus.settings` with documented defaults; `email_address` + `sent_dir` are required.

### Required
| Setting | Purpose |
|---------|---------|
| `email_address` | `str` or `{account: address}` dict; From + reply-all self-filter |
| `sent_dir` | `str` or `{account: dir}` dict; Maildir sent folder (or `None` to discard) |

### Key Optional
| Setting | Default | Purpose |
|---------|---------|---------|
| `theme` | `themes.nord` | **[DEPRECATED in config.py]** In-app picker (`t h`, `M-<`/`M->`) is authoritative, persisted in QSettings (`lazarus.conf`) |
| `theme_overrides` | `{}` | `{theme_name: {key: value}}` per-theme corrections — hex, source-palette ANSI index, named terminal color, or another Lazarus key |
| `default_heuristic` | `{}` | Replaces lines of the built-in terminal-theme mapping for every pack theme; `theme_overrides[theme]` wins over it |
| `colormap.py` | (file) | `~/.config/lazarus/themes/colormap.py` — auto-created, never overwritten, generated from `DEFAULT_TERMINAL_MAP`; defines the above two settings |
| `sync_mail_command` | `'offlineimap'` | **Fallback only**: used when `smtp_accounts` is empty; otherwise parallel `mbsync -V <acct>` runs |
| `sync_mail_interval` | `300` | Auto-sync seconds; `-1` disables |
| `send_mail_command` | `'msmtp -a "{account}" -t'` | `str` or `{account: cmd}` |
| `smtp_accounts` | `['default']` | Account names; drives parallel sync + compose From dropdown |
| `file_browser_command` | `"nautilus '{dir}'"` | Reveal-after-save; `{dir}` placeholder |
| `file_picker_command` | `None` | Optional picker; `{tempfile}` out list |
| `web_browser_command` | `''` | Empty → desktop default browser |
| `init_queries` | `['tag:inbox']` | Non-closable startup tabs (`keep_open=True`) |
| `mail_root` | `'~/Mail'` | Maildir root; Trash/Archive resolution base |
| `archive_dir` | `'~/Mail/Archive'` | Local archive destination |
| `thread_pane_position` | `'right'` | `right/left/below/above` → splitter orientation |
| `force_dark_mode` | `True` | `QTWEBENGINE_CHROMIUM_FLAGS --force-dark-mode` before web init |
| `default_to_html` | `False` | Thread view HTML default |
| `wrap_message` / `wrap_column` | `True` / `78` | Plaintext wrapping |
| `remove_temp_dirs` | `'ask'` | `always/never/ask` attachment temp cleanup |
| `gnupg_home` / `gnupg_keyid` | `None` | PGP home/key |
| `filter_rules` | `[]` | `List[Rule]` — auto after sync + `C-r` |
| `filter_scope_query` | `'tag:inbox and tag:unread'` | Scopes rules as `(scope) and (rule.query)` |
| `use_signature` | `True` | Per-account sig insertion — rich mode uses `signature.html` directly, plain mode uses `signature` |
| `tag_hotkeys` | `{}` | `{'1':'Urgent',...}` → `1-9` toggles |
| `tag_icons` | inbox/unread/attachment/sent/replied/flagged/marked/signed | NerdFont icons |
| `nerd_font` | `''` | NerdFont family for glyphs; empty = auto-pick installed `* Nerd Font`, else `tag_font` |
| `tag_order` / `hide_tags` | — | Tag display priority / hidden tags |
| `log_level` / `log_file` | `'WARNING'` / `''` | Logging; empty file → stderr |
| `html_block_remote_requests` | `True` | Block remote HTML resources |
| `html_confirm_open_links` / `_trusted_hosts` | `True` / `[]` | Link-open confirmation |
| `search_font`/`size`, `tag_font`/`size`, `message_font`/`size`, `compose_editor_font`/`size` | DejaVu Sans Mono | Fonts |
| `search_title_format` | `"{query} [{num_threads}]"` | Tab titles |
| `search_list_mode` | `'list'` | `'list'` = flat table (default), `'card'` = two-row card per thread. **Config-only** (set in `config.py`, not toggleable in-app) |
| `search_color_overrides` | `{}` | `{tag:{col:color}}` per-column overrides |
| `message_css` | — | Theme-aware CSS template (`{bg,fg,...}` placeholders) |
| `message2html_filters` | `[]` | Custom renderers `(msg→HTML\|None)` |

### Per-Account Signatures (`signature.py`)
`$XDG_CONFIG_HOME/lazarus/<account>/signature` (plain) + `signature.html` (optional), per `smtp_accounts` entry. Rich-text compose inserts `signature.html` directly (its plain-text rendering is derived via `html_utils.html_to_plain` so the block can still be located for account switches); plaintext compose (or no HTML file) uses the plain block. Swapped on `[`/`]` account cycle; placement is structural (`compose_model.sig_edit` — exact block + exact quote anchor), so no content-marker scanning and no newline drift.

## Ruly's Setup
- **Mail**: `~/Mail/` — Gmail (`RulyTafzil@gmail.com/`) + `contact@RulyTafzil.com`
- **Sync**: parallel `mbsync -V <acct>` per account (`synchronize_flags=true`)
- **Send**: `msmtp` with `gmail` + `contact` accounts
- **Theme**: Nord
- **Editor**: built-in `RichTextEditor` is the only compose editor (external `$EDITOR` flow removed)
- **Notmuch**: `post-new` hook maps folders→tags, strips Trash/Spam tags

## Development

- **Run**: `lazarus` (or `python -m lazarus`); editable `pipx install -e .`
- **Type check**: `mypy lazarus` (pipx venv) — **must stay at 0 errors**; `disallow_untyped_defs = True`
- **Lint**: `pyflakes lazarus` — clean apart from intentional re-exports in `__init__.py`/`util.py`
- **Tests**: 412 tests — `python -m pytest` from the repo root, run with the **pipx venv python** (`~/.local/share/pipx/venvs/lazarus-mail/bin/python -m pytest`). Conftest: `QT_QPA_PLATFORM=offscreen`, `AA_ShareOpenGLContexts` before QApplication, QSettings → tmp, `lazarus.notmuch` stubbed per test, `lazarus.settings` snapshot/restored, tmp Maildir fixtures.
- **Logs**: `settings.log_level='DEBUG'` + `log_file='~/.local/share/lazarus/lazarus.log'`
- **Conventions**: file headers keep `Aleks Kissinger` copyright for Dodo-derived code; Lazarus-new files carry the Ruly header. Keep `key_string`, `message_css`, `LOCAL_PROTOCOLS`, and the `lazarus.util` re-exports stable — part of the public `config.py` surface.
- **Adding keys**: add to `keymap.global_keymap` (or `tag_keymap`/`compose_keymap`/`command_bar_keymap`); local `search_keymap`/`thread_keymap` exist only for user overrides.
- **PR workflow**: NEVER commit on the mainline (`main`, formerly `master`). Branch `pr/<slug>` from `main` → push (Clanker SSH key, port 2222) → PR via `tea pr create` (`FORGEJO_TOKEN`) → Ruly tests the branch before merging → merge in PR number order, rebase any downstream branches, then delete local + remote branches. Merge style: merge commit.

### Durable gotchas (learned the hard way)
- **Quit key is `C-q`** (not `Q` — a `Q` binding for help once crashed after the AppController split; keep global commands on the controller).
- **Theme contract**: every theme must define all 19 `THEME_KEYS`; the terminal mapping emits the full set (pinned by `test_theme_import.py`). Read theme colors defensively via `style.theme_color_or()`.
- **WebEngine under pytest**: constructing `QWebEngineView`/pages segfaults offscreen — model logic is tested via stubs; only a bare scheme handler (no view) is safe (`test_webengine.py`).
- **Test widget hygiene**: shown top-level widgets (panels, windows, completer popups) must be closed/deleted at test end — garbage-collecting them mid-paint in a later test segfaults the shared offscreen QApplication. New test files should include the close/deleteLater fixture.
- **`deleteLater` vs running QThread**: closing a `ComposePanel` mid-send must not delete it (`close_panel` skips; the send-completion callback deletes). Panel timers are parented (`QTimer(self)`) so they can't fire on a dead widget.
- **`_BulkMoveWorker`**: singleton; both `batch_done` receivers (`notmuch new` + `refresh_panels`) are wired inside `actions` (`set_batch_done_listener`) so a recreated worker stays connected.
- **Thread panel double-buffer**: rapid `H`/`i` toggling leaves several web loads in flight — `_SwapGuard` ensures only the newest request may swap, each at most once.
- **Sync**: `sync_mail_command` is ignored whenever `smtp_accounts` is non-empty (default) — parallel mbsync path. `refresh_tab_titles` batches per-tab thread counts into one `notmuch count --batch`.
- **Signatures**: insertion is structural (`sig_edit`); seeds must keep `quoted_tail` populated or account-switch placement degrades to append-at-end.
- **Compose is a closed key surface**: `_allow_global_key` gates the global fallthrough in `Panel.keyPressEvent` (single-key *and* prefix-timeout paths) against `keymap.COMPOSE_ALLOWED_GLOBALS`, so list/thread hotkeys can never act on hidden panels from compose — even unbound Ctrl chords leaking up from the editor/fields. Adding a global binding that should be reachable while composing means adding it to `COMPOSE_ALLOWED_GLOBALS` + a `test_compose_keys.py` case.
- **Escape in compose is one-directional** (`escape_focus` = `self.setFocus()`): it exits the editor/fields to the chrome and never re-enters the editor — do not 'restore' the old toggle or the two-mode model breaks.
- **`notmuch count --batch`** exists (`count_batch`) — prefer it over N subprocesses (TagModel, tab titles).
- **HTML parity in reply/forward**: `notmuch show` elides `text/html` parts unless `--include-html` is passed. Every call site that produces displayed/quoted body content must pass `--include-html` (+ `--decrypt=true` for parity with the preview) or HTML-only emails reply with an **empty body** — e.g. `search._thread_latest_message` must mirror `thread_model._fetch_full_thread` (pinned by `test_list_reply_quotes_html_only_email`).
- **No eager imports in `__init__.py`**: Importing submodules like `themes` must not transitively import `app` or PyQt6/WebEngine. CLI developer tools (`tools/import_themes.py`) must remain pure Python stdlib with 0 external dependencies so they run on standard system Python without requiring a venv.
- **ANSI escape sequences throw off terminal string width padding**: 24-bit truecolor escape codes (`\033[48;2;...m`) have byte length but zero terminal column width. When formatting multi-column CLI outputs, pad the visible text components with fixed widths rather than wrapping the whole colorized string in `<width>`, otherwise columns will be misaligned.
- **Topological dependency order in theme key resolution**: In `terminal_theme_to_lazarus`, evaluate keys in dependency order (`bg`, `fg`, `fg_dim` before dependent keys like `fg_date` and `fg_subject_irrelevant`).
- **Contrast in custom item delegates vs standard Qt widgets**: Standard Qt widgets (`TagPanel` / `QTreeView`) automatically invert text to `QPalette.HighlightedText` on selection. Custom item delegates (`CardDelegate` in `search.py`) manually paint text pens; painting an opaque bright `bg_highlight` without inverting text causes an unreadable contrast clash. Use a modern 25% alpha blend wash (`bg_highlight` over `bg` when high-contrast) so distinct column colors (`fg_from`, `fg_subject_unread`, `fg_tags`) stay readable on any theme.
- **Fine-grained row removals vs model reset**: In `SearchModel.refresh_thread`, use `beginRemoveRows` / `endRemoveRows` when a thread leaves a query rather than `beginResetModel`. Full resets blow away scroll position, delegate state, and cursor focus.
- **Non-blocking `notmuch new` in bulk thread actions**: Run `notmuch.new()` inside the `_BulkMoveWorker` QThread right after file renames rather than stalling the Qt main thread. Guard destructive actions (`delete_thread`, `archive_thread`) from running redundant shell subprocesses if no threads are marked.
- **Lazy initialization of secondary windows & WebEngine warmup**: Never instantiate dialogs (`HelpWindow`) during `app.py` bootstrap. Defer Chromium warmup to `QTimer.singleShot(0, self._warm_webengine)` to drop cold startup from ~760ms to ~110ms.
- **1-to-1 mapping vs convoluted fallback chains**: Multi-item fallback chains (`[14, 6, 12, 4, 'fg']`) were relics of 8-color vs 16-color VT100 terminals. In modern truecolor Qt GUI apps where 100% of bundled themes define all 16 colors, clean 1-to-1 mappings are vastly clearer and maintainable.
- **URL percent-decoding in REST routes**: Browser clients URL-encode path parameters (e.g. `@` as `%40` in message IDs like `CABsu...%40mail.gmail.com`). Always unquote path segments with `urllib.parse.unquote()` before querying `notmuch show -- id:...` or file actions, otherwise notmuch searches for literal `%40` and fails to find the message.
- **Headless daemon independence from Qt**: NED runs without instantiating Qt. Modules the daemon reuses (`config.py`, `signature.py`) must keep their standard XDG path resolution fallbacks (`$XDG_CONFIG_HOME` / `~/.config`) — `PyQt6.QtCore.QStandardPaths` may be unimportable in a headless environment.
- **`A` archive parity on mobile**: In Lazarus, `a` is tag-only (`-inbox -unread`), whereas `A` (`archive_to_local`) removes `-inbox -unread`, moves message files into `~/Mail/Archive/cur/` while stripping UID annotations, and runs `notmuch new`. The mobile interface uses `A` archive for all archive actions.
- **Pull-down-to-sync gesture on mobile web**: Implemented using passive touch handlers (`touchstart`, `touchmove`, `touchend`) on the thread list when `scrollTop <= 0`. Pulling past the threshold triggers `POST /api/sync`, executing parallel `mbsync -V <account>` processes, running `notmuch new`, and applying filter rules.
- **Plaintext signature placement and switching**: `sig_edit()` in `compose_model.py` is a pure function. On mobile, signatures are pre-populated above the quote anchor, and switching accounts dynamically replaces the signature block in the `<textarea>` without network round-trips.
- **No-cache headers on static PWA assets**: Static files (`app.js`, `app.css`) served by NED must use `Cache-Control: no-cache, must-revalidate` so client updates are immediately applied upon browser refresh.
- **Zero Qt imports in `lazarus.core`**: The `lazarus.core` package is strictly headless with zero Qt dependencies. Any background threading must use standard library `threading.Thread`, not `QThread`.
- **`threading.Thread` vs Qt main loop signal marshalling**: When background threads in `core.actions` complete batches, wire desktop listeners through `_QtBatchDoneBridge(QObject)` with a `pyqtSignal()`. Emitting the signal safely marshals the callback onto Qt's main event loop before invoking `app.refresh_panels()`.
- **Non-blocking socket select polling for SSE streams**: On Unix domain sockets, standard blocking `readline()` does not reliably wake up when another thread calls `shutdown()` or `close()`. Using `select.select([raw_sock], [], [], 0.2)` with non-blocking sockets allows event listener threads to check stop events and shut down cleanly within milliseconds without hanging during test teardown.
- **`core.actions._get_collector()` is a swappable seam, not dead code**: move actions resolve the collector through `lazarus.actions.collect_files` when it differs from core's — that's how tests inject file lists (`monkeypatch.setattr(actions, 'collect_files', ...)` in `tests/test_maildir_concurrency.py` and the desktop path) and how a UI-level collector could replace core collection. Do not “simplify” it away; the headless daemon resolves it to the core collector since `lazarus.actions` is never imported there.
- **`is_ned_active()` is TTL-cached** (2s, keyed on the NED env vars) because it sits on every hot path (panel refresh, keypress actions, autocomplete). Polling loops that must see a fresh answer (daemon spawn) call `is_ned_active(force=True)`.
- **Desktop expunge is routed through the daemon when NED is active**: `POST /api/v1/expunge` (under `mutation_lock`) so the irreversible trash flagging runs inside the daemon's single-writer boundary; the local `actions.expunge_trash()` path is the NED-less fallback only.
- **`get_part_data` canonical order is `(content, content_type, filename)`** across `core.service`, `ned.handler`, and `NedClient.get_part_data`. Keep the layers in that order — the sibling tests (`test_ned.py`, `test_ned_client.py`) pin it.
- **Sync summary is single-sourced**: `SyncMailThread` stores `via_ned`/`sync_message`; when the daemon ran the sync its pre-formatted summary is shown as-is, otherwise `parse_sync_stats()` (core.sync) feeds the status bar. Never re-implement the `Far:` regex in the controller.
- **SSE invalidations are debounced** (150ms single-shot timer in `AppController`) so a desktop action that mutates via NED — which triggers both the local refresh and a daemon-broadcast invalidation — coalesces into one panel pass.
- **Cascading config resolution**: NED searches for its own configuration at `~/.config/ned/config.py` first, then falls back to `~/.config/lazarus/config.py`.
- **Keybinding semantics under NED**: `archive_thread` for key `a` removes `inbox` and `unread` tags via `modify_tags` without moving files. `archive_to_local` for key `A` moves maildir files to the local archive folder via `archive_thread`. Desktop client actions must never redirect key `a` to file moving daemon archive endpoints.
- **Module aliasing in `sys.modules` for decoupled modules**: When a legacy module name must keep resolving to a moved service (e.g. tests that patched `lazarus.server.service`), assign `sys.modules[__name__] = _core_service` instead of re-exporting symbols. This ensures monkeypatches on either module name modify the identical module object in memory.
- **NED static asset bundling**: NED serves web client assets directly out of `lazarus/ned/static/`. Package data in `setup.py` includes `ned/static/*` so wheel and source distributions ship the web client.

### Architecture and roadmap

#### NED architectural principles and YAGNI guardrails
1. **Explicitly for Notmuch**: NED is not a generic storage platform. It preserves native Notmuch query syntax (`tag:inbox AND date:2w..today`) and identifiers (`thread:...`, RFC Message-IDs).
2. **Single concurrency boundary**: Notmuch permits concurrent readers, but only a single writer can modify the Xapian index at any time. NED owns the single serialized write queue (`MutationLock`). Clients request mutations, and NED executes them sequentially.
3. **SSE for cache invalidation, not state replication**: Server-Sent Events broadcast minimal invalidation signals (`thread`, `threads`). Clients re-query NED when an event affects the active view rather than reconstructing state through delta patching.
4. **Single-user simplicity**: Local IPC uses standard Linux filesystem permissions on a Unix domain socket. Remote network access uses Tailscale WireGuard encryption with a single bearer token.
5. **Synchronous mutations without heavy job queues**: Tagging and file moves execute in milliseconds. Long-running IMAP sync runs with a busy lock and broadcasts an SSE completion event.
6. **Unified daemon with bundled web assets**: NED serves the mobile PWA web assets directly on `/` and `/static/`.

#### Transports and IPC
- **Local IPC (Unix domain socket)**: `/run/user/$UID/ned/ned.sock` (or `~/.local/share/lazarus/ned/ned.sock`). Communicates using HTTP/1.1 over Unix domain stream sockets with sub-millisecond latency.
- **Remote network (Tailscale)**: Binds to the host Tailscale WireGuard address (`100.x.y.z:8080`) or `127.0.0.1`. Refuses unauthenticated `0.0.0.0` bindings.
- **Systemd service**: Unit file provided at `contrib/ned.service` for user systemd management (`systemctl --user enable --now ned`).

#### API v1 specification
All endpoints are versioned under `/api/v1/` with legacy aliases under `/api/`:

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/threads` | Search threads (`q`, `limit`, `offset`). |
| `GET` | `/api/v1/threads/{id}` | Fetch full thread tree with messages and metadata. |
| `GET` | `/api/v1/messages/{id}/parts/{part_id}` | Download decoded message body part or binary attachment. |
| `POST` | `/api/v1/tags` / `/api/v1/tag` | Modify tags (`{"queries": [...], "add": [...], "remove": [...]}` or legacy `ids`). |
| `POST` | `/api/v1/threads/{id}/archive` | Archive thread (`-inbox -unread` + move to `Archive/cur/`). |
| `POST` | `/api/v1/threads/{id}/trash` | Trash thread (`+trash -inbox -unread` + move to `Trash/cur/`). |
| `POST` | `/api/v1/threads/{id}/unarchive` | Restore archived thread to `inbox`. |
| `POST` | `/api/v1/threads/{id}/untrash` | Restore trashed thread from `Trash/cur/` to `INBOX/cur/`. |
| `POST` | `/api/v1/expunge` | Flag every `tag:trash` file with the Maildir `T` flag (irreversible). |
| `POST` | `/api/v1/threads/{id}/star` | Toggle flagged tag (`{"flag": bool}`). |
| `GET` | `/api/v1/tags` | List all known Notmuch tags with thread counts. |
| `GET` | `/api/v1/contacts` | Address autocomplete matching prefix `q`. |
| `GET` | `/api/v1/accounts` | List configured sender accounts: `{"accounts": [...]}`. |
| `GET` | `/api/v1/signatures` | Per-account signature map: `{"use_signature": bool, "signatures": {...}}`. |
| `GET` | `/api/v1/reply-seed` | Generate reply recipient headers, quoted body, and signature. |
| `POST` | `/api/v1/send` | Send outbound message via `msmtp`. |
| `POST` | `/api/v1/sync` | Trigger IMAP sync + `notmuch new` + filter rules. |
| `GET` | `/api/v1/events` | Server-Sent Events (SSE) stream for cache invalidation. |
| `GET` | `/` | Serves bundled mobile PWA web application. |

#### Invalidation event schema
The SSE stream (`GET /api/v1/events`) emits JSON events:
```text
event: invalidate
data: {"scope": "threads", "reason": "sync"}

event: invalidate
data: {"scope": "thread", "id": "0000000000001234", "reason": "tag"}
```

#### Notmuch Email Daemon (NED) roadmap
1. Phase 1 (completed): Daemon implementation (`lazarus.ned`) with dual listeners (Unix domain socket + optional Tailscale TCP), `MutationLock` serialized write queue, `/api/v1/` routes, SSE invalidation publisher, and cascading config resolution.
2. Phase 2 (completed): Zero-dependency Python client library (`lazarus.ned.client` and `ned_client.py`) supporting Unix socket and HTTP transports, SSE stream parsing, typed signatures for all routes, and automated unit tests.
3. Phase 3 (completed): Migrate Lazarus desktop to pure client, replacing local `_BulkMoveWorker` and direct Notmuch subprocess calls with `NedClient`, wired to SSE invalidation stream.
4. Phase 4 (completed): Retire `lazarus-server` in favor of `ned`, then remove `lazarus.server` + the `lazarus-web`/`lazarus-server` entry points entirely (2026-09).

#### Decoupled domain engine (`lazarus.core`)
The codebase follows a clear separation between headless domain primitives and presentation layers:
- `lazarus.core`: Pure Python domain logic (file moves, mail sync, index queries). Zero Qt dependencies.
- `lazarus.ned`: The daemon — HTTP/REST over Unix socket/TCP, SSE, serialized mutations.
- `lazarus` (root): Desktop client in PyQt6/WebEngine consuming `lazarus.core` via `NedClient`.

#### Migration roadmap for `core/`
To prevent massive diff churn and preserve `git blame`, headless modules are migrated incrementally when touched:
1. Steps 1 and 2 completed: Decoupled `core.actions` and `core.sync`.
2. Step 3 completed: Decoupled `core.service`; `ned.handler` consumes it directly (the `server.service` sys.modules alias was removed with the server in Phase 4).
3. Candidate modules for incremental migration into `core/`:
   - `core/rules.py` (filter engine)
   - `core/notmuch.py` (CLI wrapper)
   - `core/mail_utils.py` (MIME structure and attachment extraction)
   - `core/compose_model.py` (reply seeds, quote formatting, signature placement)
   - `core/mime_builder.py` (outbound MIME assembly)
4. Backward compatibility rule: Whenever a module moves to `core/`, maintain a one-line re-export shim at `lazarus/<module>.py` so user configurations in `~/.config/lazarus/config.py` remain unbroken.
5. Desktop UI panels (`mainwindow`, `search`, `thread`, `compose`, `editor`, `commandbar`, `tag`) remain at the root package level.

### Shelved work

None — the previous shelves (compose-pane HTML fidelity `.plan` on `pr/html-replies`) were pruned 2026-08-20.

