#     Lazarus - A fork of Dodo, a graphical, hackable email client based on notmuch
#     Copyright (C) 2021 - Aleks Kissinger
#     Copyright (C) 2025 - Ruly Tafzil
#
# This file is part of Lazarus
#
# Lazarus is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Lazarus is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Lazarus. If not, see <https://www.gnu.org/licenses/>.
"""
This module holds settings and sets their default values. The values set
here should be overridden by the user in `~/.config/lazarus/config.py`. This
can be done as follows:

.. code-block:: python

  import lazarus
  lazarus.settings.email_address = 'First Last <me@domain.com>''
  lazarus.settings.sent_dir = '~/mail/work/Sent'

The settings :func:`~lazarus.settings.email_address` and
:func:`~lazarus.settings.sent_dir` are required. Lazarus may not work correctly
unless you set them properly. The rest of the settings have reasonable
defaults, as detailed below.
"""

from __future__ import annotations
from . import themes
from . import rules
from typing import Literal, Dict, List, Union, Any

# functional
email_address: Union[str, Dict[str, str]] = ''
"""Your email address (REQUIRED)

This is used both to populate the 'From' field of emails and to (mostly)
avoid CC'ing yourself when replying to all. It can be given as 'NAME <ADDRESS@DOMAIN>'
format. For just one email address, this can be given as a string. From multiple
emails, use a dictionary mapping the account names in :func:`~lazarus.settings.smtp_accounts`
to the associated email addresses.
"""

sent_dir = ''
"""Where to store sent messages (REQUIRED)

This will usually be a subdirectory of the Maildir sync'ed with
:func:`~lazarus.settings.sync_mail_command`. This setting can be given either
as a string to use one global sent directory, or as a dictionary mapping
account names in :func:`~lazarus.settings.smtp_accounts` to their own sent dirs.

A value of None, either standalone or as one of the dict value, can be used to
indicate the email should be discarded. This can be useful if the sendmail
command already has a mechanism for that feature.
"""

file_browser_command = "nautilus '{dir}'"
"""Command used to launch external file browser (reveal-after-save).

Shell command with ``{dir}`` placeholder. Run after attachments are saved
to *reveal* the destination folder. Set to ``""`` to skip the reveal and
just show a status message. Used by ``O`` in the thread view when
:data:`attachment_reveal` is ``'file_browser'``.
"""

attachment_save_dir = '~/Downloads'
"""Default directory for the ``O`` (save attachments) picker.

Single directory string. Expanded with ``~``. The picker opens here by
default; the user then chooses the sibling that will receive *all*
attachments from the current message.
"""

attachment_reveal: Literal['file_browser', 'none'] = 'file_browser'
"""What to do after attachments are saved with ``O``.

``'file_browser'`` — run :data:`file_browser_command` on the destination.
``'none'`` — show a status message only.
"""

file_picker_command = None
"""Command used to launch external file picker

This is an optional shell command, which additionally takes the `{tempfile}` placeholder.
This command is used when picking files to attach to an email. The command should write
out the chosen files to {tempfile}, which will then be read and deleted, if it exists.

By default, this is set to None, in which case the built-in file picker will be used.
"""

web_browser_command = ''
"""Web browser to use when clicking links in emails

This should be a single command which expects a URL as its first argument. If this
is an empty string, Lazarus will attempt to use the default web browser supplied by
the desktop environment, if it exists.
"""

send_mail_command: str | dict[str, str] = 'msmtp -a "{account}" -t'
"""Command used to send mail via SMTP

Either a plain command or a mapping of account names to command.

The command must be a shell command that expects a (sendmail-compatible) email
message to be written to STDIN. Note that it should read the destination from
the `From:` header of the message and not a command-line argument. Use the
`{account}` placeholder to read the currently selected account.

"""

smtp_accounts = ['default']
"""A list of SMTP account names recognised by `send_mail_command`

This setting allows switching SMTP accounts in the Compose panel. The first account
in the list is selected by default.

Note this also selects the sync path: with a non-empty list, mail sync runs
``mbsync -V <account>`` per account and :func:`~lazarus.settings.sync_mail_command`
is ignored (set ``[]`` here to use the shell command instead).
"""

sync_mail_command = 'offlineimap'
"""Shell command used to sync IMAP with local Maildir (fallback path)

Only used when :func:`~lazarus.settings.smtp_accounts` is empty.  With any
accounts configured — the default is ``['default']`` — syncing instead runs
``mbsync -V <account>`` for each account in parallel and this command is
ignored.  Set ``smtp_accounts = []`` to force the shell-command path.
"""

sync_mail_interval = 300
"""Interval to run :func:`~lazarus.settings.sync_mail_command` automatically, in seconds

Set this to -1 to disable automatic syncing.
"""

default_to_html = False
"""Open messages in HTML mode by default, rather than plaintext"""

wrap_message = True
"""Hard-wrap message text by default

You may wish to disable this if you don't want hard wraps in your email messages or
your text editor does hard wrapping already.
"""

wrap_column = 78
"""Wrap text to this column when composing emails
"""

remove_temp_dirs = 'ask'
"""Set whether to remove temporary directories when closing a panel

Thread panels create temporary directories to open attachments. These can be cleaned up
automatically when a panel (or Lazarus) is closed. Possible values are: 'always', 'never',
or 'ask'.
"""

default_thread_list_mode: Literal['conversation', 'thread'] = 'conversation'
"""Set the way your thread should be listed.

Possible values are:
    * 'conversation': flat list, chronologically sorted
    * 'thread': tree view, following the various subthreads
"""

gnupg_home = None
"""Directory containg GnuPG keys

If set to None, GnuPG will use whatever directory is the default (consult the
GnuPG documentation for more information on what this might be).
"""

gnupg_keyid = None
"""The id of the key to be used for GnuPG-signing mail messages.

If set to the id of a valid GnuPG private signing key, sent messages will be
cryptographically signed according to rfc3156 using the GnuPG sotware, which
should be installed and configured.  Requires python-gnupg
(https://pypi.org/project/python-gnupg/)"""

init_queries = [ 'tag:inbox' ]
"""List of non closable queries open at startup

You can save query with `notmuch config set query:inbox "tag:inbox and not
tag:trash"` and use `query:inbox` as a search term.

"""

mail_root = '~/Mail'
"""Root directory of the local Maildir.

This is used by delete/archive operations to locate per-account Trash
folders and the local Archive.  Change this if your mail lives elsewhere.
"""

thread_pane_position: Literal['right', 'left', 'below', 'above'] = 'right'
"""Where to place the persistent thread preview pane.

Valid values: ``'right'``, ``'left'``, ``'below'``, ``'above'``.
The thread pane is always visible alongside the list tabs.
"""

force_dark_mode = True
"""Pass ``--force-dark-mode`` to Chromium so empty render surfaces
match the theme instead of flashing white.
"""

archive_dir = '~/Mail/Archive'
"""Path to a local-only Maildir where ``A`` hotkey moves archived emails.

Files are moved into a ``cur/`` subdirectory here, keeping them
searchable in notmuch while removing them from synced IMAP folders.
This directory should be under mail_root but outside all mbsync
channels so archived mail stays local-only.
"""

no_hooks_on_send = True
"""disable/enable calling notmuch hooks when sending email

When True, 'notmuch new' is called with --no-hooks when a message is sent. One
may not wanting to wait for the hooks on each sent email, for example when
calling mbsync on their notmuch hooks. Other users may set this to False, for
example when notmuch hooks are used to archive sent mail."""

compose_editor_font = 'DejaVu Sans Mono'
"""Font used in the built-in compose editor."""

compose_editor_font_size = 12
"""Font size for the built-in compose editor."""

compose_autocomplete_min_chars = 2
"""Minimum characters before address autocomplete triggers."""

use_signature = True
"""Whether to automatically insert a per-account signature when composing.

Signatures are loaded from files (not from this settings module -- see
:mod:`lazarus.signature`), one per account:

.. code-block:: text

  $XDG_CONFIG_HOME/lazarus/<account>/signature       (plain text)
  $XDG_CONFIG_HOME/lazarus/<account>/signature.html   (HTML)

where ``<account>`` is one of the names in
:func:`~lazarus.settings.smtp_accounts` ($XDG_CONFIG_HOME defaults to
``~/.config``). Either file is optional; if only ``signature.html``
exists its plaintext rendering (via :func:`~lazarus.util.html2text`) is
used until Lazarus has a rich-text compose mode. Set this to False to
disable signature insertion entirely.
"""

filter_rules: List[rules.Rule] = []
"""A list of :class:`lazarus.rules.Rule` mail filters, applied automatically
after every sync (and on demand via the ``C-r`` keybinding).

Each rule is a notmuch query plus tags to add/remove from anything
matching it. See the "Mail filters" section of README.md for a worked
example.
"""

filter_scope_query = 'tag:inbox and tag:unread'
"""Notmuch query limiting which mail :func:`~lazarus.settings.filter_rules`
are allowed to touch.

Rules are applied as ``(filter_scope_query) and (rule.query)``, so
this is what keeps a new/changed rule from re-tagging your entire
archive the next time it runs -- it should describe "freshly arrived,
not yet triaged" mail. The default, newly-synced unread inbox mail, is
a reasonable definition of that for most setups.
"""

# logging
log_level = 'WARNING'
"""Python logging level for Lazarus.

One of ``'DEBUG'``, ``'INFO'``, ``'WARNING'``, ``'ERROR'``, ``'CRITICAL'``.
Set to ``'DEBUG'`` when troubleshooting, ``'WARNING'`` for normal use.
"""

log_file = ''
"""Path to a log file.  If empty, logs go to stderr only.

Set to e.g. ``'~/.local/share/lazarus/lazarus.log'`` to persist logs across
sessions for diagnostics.
"""

# security
html_block_remote_requests = True
"""Block remote requests for HTML messages

HTML messages, especially from dodgy senders, can display remote content or 'call home'
from embedded image tags or iframes. If set to True, Lazarus will not allow these requests.
"""

html_confirm_open_links = True
"""Display a confirmation dialog before opening a link in browser

If this is True, Lazarus will display a confirmation dialog showing the *actual* URL that
the web browser will request before opening. This is an extra measure against phishing
or emails opening your web browser without your permission.
"""

html_confirm_open_links_trusted_hosts: List[str] = []
"""A list of trusted hosts for HTML links.

If a link is to a host in this list, it will be opened without confirmation, even if
:func:`~lazarus.settings.html_confirm_open_links` is True.
"""

# visual
theme = themes.nord
"""The GUI theme

A theme is a dictionary mapping a dozen or so named colors to HEX values.
Several themes are defined in `lazarus.themes`, based on the popular Nord,
Solarized and Gruvbox color palettes. Hundreds more are available by name
(e.g. ``themes.REGISTRY['Dracula']``) via the bundled terminal-theme
library and any packs found in ``~/.config/lazarus/themes/*.json`` -- see
`lazarus.themes.build_registry`.
"""

theme_overrides: Dict[str, Dict[str, str | int]] = {}
"""Per-theme color corrections, keyed by theme name.

Terminal-style themes (the bundled library and any user JSON packs) are
mapped to Lazarus's color keys by a best-effort heuristic -- it won't
always pick the color you'd choose by hand. Use this to hand-correct
specific keys for one theme, without editing the source pack. To change
the heuristic itself for *every* theme, use `default_heuristic` instead.

Each value can be:

* a literal hex color: ``'fg_link': '#8be9fd'``
* an ANSI palette index (0-15) of the *source* theme entry: ``'fg_subject_unread': 3``
  uses that theme's palette color 3
* a named terminal color of the *source* theme: ``'fg_tags': 'foreground'``,
  or ``'background'`` / ``'foreground'`` / ``'cursor-color'`` /
  ``'selection-background'`` / ``'selection-foreground'``
* another Lazarus key of the same mapped theme: ``'fg_date': 'fg_dim'``

Example::

    theme_overrides = {
        'Dracula': {
            'fg_link': '#8be9fd',
            'fg_subject_unread': 3,        # palette yellow
        },
    }

Only the listed keys are overridden; everything else stays as mapped.
Palette-index and named-color references resolve only for pack themes
(which have a source palette) -- hex values apply everywhere.
"""

default_heuristic: Dict[str, str | int] = {}
"""Replace lines of the built-in terminal-theme heuristic, for every
pack theme, without editing the source pack.

The heuristic maps terminal-theme entries (16 ANSI palette colors +
background/foreground/cursor-color/selection-*) onto Lazarus's 19
semantic color keys (see ``lazarus.themes.DEFAULT_TERMINAL_MAP``). Any
key listed there can be overridden here; values use the same forms as
`theme_overrides` (hex, palette index, named terminal color, or another
Lazarus key). `theme_overrides['ThemeName']` runs after this and wins::

    default_heuristic = {
        'fg_subject': 2,               # palette green, every theme
        'fg_tags': 'foreground',
        'fg_date': 'fg_dim',
    }

Affects pack (terminal-style) themes only -- hand-written themes
(nord, ...) are literal palettes, not heuristic products.
"""

search_font = 'DejaVu Sans Mono'
"""The font used for search output and various other list-boxes"""

search_font_size = 13
"""The font size used for search output and various other list-boxes"""

tag_font = 'DejaVu Sans Mono'
"""The font used for tags and tag icons"""

tag_font_size = 13
"""The font size used for tags and tag icons"""

message_font = 'DejaVu Sans Mono'
"""The font used for plaintext messages"""

message_font_size = 12
"""The font size used for plaintext messages"""

search_view_padding = 1
"""A bit of spacing around each line in the search panel"""

tag_hotkeys: dict[str, str] = {}
"""Number keys mapped to tags for quick toggling.

Map key strings (``'1'``-``'9'``) to tag names.  Pressing the key in a
search panel toggles that tag on the selected thread.

Example::

  lazarus.settings.tag_hotkeys = {'1': 'Urgent', '2': 'ToDo', '3': 'spam'}
"""

search_title_format = "{query} [{num_threads}]"
"""A Python format string for the tab title of search panels

The following placeholders can be used:

- {query}: the current search query
- {num_threads}: the number of threads returned by the search
"""

tag_icons = {
  'inbox': '',
  'unread': '',
  'attachment': '',
  'sent': '>',
  'replied': '',
  'flagged': '󰉀',
  'marked': '',
  'signed': '',
}
"""Tag icons

This is a dictionary of substitutions used to abbreviate common tag names as unicode
icons in the search and thread panels.
"""

nerd_font = ''
"""NerdFont family used for icon glyphs (toolbar buttons, dropdown arrow).

Empty (default) auto-picks the first installed family whose name
contains ``'Nerd Font'``; if none is installed, falls back to
:func:`~lazarus.settings.tag_font` (whose private-use-area glyphs still
render via Qt font fallback).  Set explicitly to pin a family, e.g.
``'CaskaydiaMono Nerd Font'``.
"""

tag_order: list[str] = ['marked', 'Urgent', 'ToDo', 'Waiting', 'Reference', 'inbox', 'sent']
"""Tag display order in the tags column.

Tags listed here appear first (in this order).  Any tags not listed
follow in alphabetical order.  Set to ``[]`` for pure alphabetical.
"""

hide_tags = ['unread', 'sent']
"""Tags to hide in search panel"""

message_css = """
pre {{
  font-family: {message_font};
  font-size: {message_font_size}pt;
}}

pre .quoted {{
  color: {fg_dim};
}}

pre .headername {{
  color: {fg_bright};
  font-weight: bold;
}}

pre .headertext {{
  color: {fg_bright};
}}

body {{
  background-color: {bg};
  color: {fg};
}}

::-webkit-scrollbar {{
  background: {bg};
}}

::-webkit-scrollbar-thumb {{
  background: {bg_button};
}}

::selection {{
  color: {bg};
  background: {fg};
}}

a {{
  color: {fg_bright};
}}
"""
"""CSS used in view and compose window

Placeholders may be included in curly brackets for any color named in the current theme, as
well as {message_font} and {message_font_size}. Literal curly braces should be doubled, i.e.
'{' should be '{{' and '}' should be '}}'.
"""

message2html_filters: List[Any] = []
"""A list of functions to extract text from a mail message JSON.

Every item in this list should be a function, which either returns a HTML string
(which gets formatted inside a ``<pre>`` tag), or returns ``None``. The first
function to return a non-``None`` value is used to render the message. If all functions
return ``None``, the default rendering is used.

The default rendering runs the following functions in order, which might also be useful
when writing your own filters:

- :func:`~lazarus.util.body_text` (to get a body string from the JSON)
- :func:`~lazarus.util.simple_escape` (to make the string HTML-safe)
- :func:`~lazarus.util.colorize_text` (to colorize quoted text)
- :func:`~lazarus.util.linkify` (to detect URLs)

Example configuration using this feature to highlight markdown syntax:

.. code-block:: python

  import pygments.formatters
  from lazarus import util

  def render_github(msg):
      # Double imports needed due to how dodo runs config.py
      import pygments.lexers
      import pygments.formatters

      # If you use some sort of auto-tagging, you might want to match on
      # tags instead of headers.
      if "headers" not in msg or "From" not in msg["headers"]:
          return None
      if not msg["headers"]["From"].endswith("<notifications@github.com>"):
          return None

      text = util.body_text(msg)
      lexer = pygments.lexers.MarkdownLexer()
      formatter = pygments.formatters.HtmlFormatter(nowrap=True)
      highlighted = pygments.highlight(text, lexer, formatter)
      return util.linkify(highlighted)

  lazarus.settings.message2html_filters = [render_github]

  # Available styles: https://pygments.org/styles/
  pygments_css = pygments.formatters.HtmlFormatter(style="gruvbox-dark").get_style_defs()
  lazarus.settings.message_css += pygments_css.replace("{", "{{").replace("}", "}}")
"""

search_color_overrides: Dict[str, Dict[str, str]] = {}
"""A dictionary mapping tags to color dictionaries.

The color dictionaries map columns to override colors.
The available columns are:

- date
- from
- subject
- tags

For example, to show a red subject for messages tagged 'urgent',
using the built-in Gruvbox palette:

.. code-block:: python

  lazarus.settings.search_color_overrides = {
      'urgent': {
          'subject': lazarus.themes.gruvbox_p['neutral_red'],
      }
  }
"""
