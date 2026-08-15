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
from typing import Any, Callable, Dict, Tuple

from .protocols import PanelApp

KeyBinding = Tuple[str, Callable[..., Any]]
Keymap = Dict[str, KeyBinding]

# ── Consolidated global keymap ────────────────────────────────────────
#
# In the split-pane layout the search list and the thread
# preview are always visible.  Every key does exactly one thing,
# delegating to the list or the thread preview directly — no key
# changes behaviour depending on focus, and no <escape> preamble is
# needed.
#
# List keys       → app.delegate_to_list()
# Thread keys     → app.delegate_to_thread()
# Global keys     → app methods (open_search, sync_mail, …)
#
# The receiver is always the ``PanelApp`` the panel was built with — in
# practice the ``AppController`` (see :mod:`lazarus.protocols`).

global_keymap: Keymap = {
  # ── Thread list ──────────────────────────────────────────────────
  'j':           ('next thread', lambda a: a.navigate_list('next')),
  'k':           ('previous thread', lambda a: a.navigate_list('previous')),
  '<down>':      ('next thread', lambda a: a.navigate_list('next')),
  '<up>':        ('previous thread', lambda a: a.navigate_list('previous')),
  '<tab>':       ('next unread', lambda a: a.delegate_to_list('next_thread', unread=True)),
  'S-<tab>':     ('previous unread', lambda a: a.delegate_to_list('previous_thread', unread=True)),
  'g g':         ('first thread', lambda a: a.delegate_to_list('first_thread')),
  'G':           ('last thread', lambda a: a.delegate_to_list('last_thread')),
  'M-j':         ('down 20', lambda a: [a.delegate_to_list('next_thread') for _ in range(20)]),
  'M-k':         ('up 20', lambda a: [a.delegate_to_list('previous_thread') for _ in range(20)]),
  '<pageup>':    ('page up (list)', lambda a: a.delegate_to_list('prev_page')),
  '<pagedown>':  ('page down (list)', lambda a: a.delegate_to_list('next_page')),
  '<enter>':     ('open thread', lambda a: a.delegate_to_list('open_current_thread')),
  'u':           ('toggle unread', lambda a: a.delegate_to_list('toggle_thread_tag', tag='unread')),
  'f':           ('toggle flagged', lambda a: a.delegate_to_list('toggle_thread_tag', tag='flagged')),
  's':           ('mark and advance', lambda a: a.mark_and_advance()),
  'a':           ('archive', lambda a: a.delegate_to_list('archive_thread')),
  'A':           ('archive to local', lambda a: a.delegate_to_list('archive_to_local')),
  'd':           ('delete', lambda a: a.delegate_to_list('delete_thread')),
  'd d':         ('empty trash', lambda a: a.expunge_trash()),
  'd u':         ('restore from trash', lambda a: a.delegate_to_list('restore_thread_from_trash')),

  # ── Message viewer ───────────────────────────────────────────────
  'J':           ('next message', lambda a: a.delegate_to_thread('next_message')),
  'K':           ('previous message', lambda a: a.delegate_to_thread('previous_message')),
  'M':           ('toggle thread list mode', lambda a: a.delegate_to_thread('toggle_list_mode')),
  '<space>':     ('page down (message)', lambda a: a.delegate_to_thread('scroll_message', pages=1)),
  '-':           ('page up (message)', lambda a: a.delegate_to_thread('scroll_message', pages=-1)),
  'H':           ('toggle HTML', lambda a: a.delegate_to_thread('toggle_html')),
  'i':           ('toggle remote images', lambda a: a.delegate_to_thread('toggle_remote_content')),
  'r':           ('reply to all', lambda a: a.reply(to_all=True)),
  'R':           ('reply', lambda a: a.reply(to_all=False)),
  'C-y':         ('forward', lambda a: a.forward()),
  'O':           ('open attachments', lambda a: a.delegate_to_thread('open_attachments')),
  '<escape>':    ('focus list', lambda a: a.main_window.focus_list()),
  'C-<enter>':   ('close thread preview', lambda a: a.main_window.clear_thread()),

  # ── Message-level actions (Ctrl-variants act on the selected message
  #    in the thread preview; plain keys act on the whole thread) ──────
  'C-u':         ('toggle unread (message)', lambda a: a.delegate_to_thread('toggle_message_unread')),
  'C-f':         ('toggle flagged (message)', lambda a: a.delegate_to_thread('toggle_message_flagged')),
  'C-a':         ('archive (message)', lambda a: a.delegate_to_thread('archive_message')),
  'C-A':         ('archive to local (message)', lambda a: a.delegate_to_thread('archive_message_to_local')),
  'C-d':         ('delete (message)', lambda a: a.delegate_to_thread('delete_message')),
  'C-t':         ('tag (message)', lambda a: a.tag_message_bar()),

  # ── Global ───────────────────────────────────────────────────────
  '?':           ('show help', lambda a: a.show_help()),
  'C-q':           ('quit', lambda a: a.prompt_quit()),
  '`':           ('sync mail', lambda a: a.sync_mail(quiet=False)),
  'C-r':         ('apply filter rules', lambda a: a.apply_filter_rules()),
  'l':           ('next panel', lambda a: a.next_panel()),
  'h':           ('previous panel', lambda a: a.previous_panel()),
  'x':           ('close panel', lambda a: a.close_panel()),
  'X':           ('close all', lambda a: [a.close_panel(i) for i in reversed(range(a.num_panels()))]),
  'c':           ('compose', lambda a: a.open_compose()),
  'I':           ('show inbox', lambda a: a.open_search('tag:inbox')),
  'U':           ('show unread', lambda a: a.open_search('tag:inbox and tag:unread')),
  'F':           ('show flagged', lambda a: a.open_search('tag:flagged')),
  'T':           ('show tags', lambda a: a.open_tags()),
  '/':           ('search', lambda a: a.search_bar()),
  'C-/':         ('edit search query', lambda a: a.edit_search_query()),
  't t':         ('tag', lambda a: a.tag_bar()),
  't m':         ('tag all marked', lambda a: a.tag_bar(mode='tag marked')),
}
"""The global keymap

Every key delegates either to the thread list or the thread preview
pane directly (via :func:`~lazarus.controller.AppController.delegate_to_list` or
:func:`~lazarus.controller.AppController.delegate_to_thread`), so all bindings work
regardless of which pane has keyboard focus.
"""

# Add configurable tag hotkeys (1-9) to the global keymap.
for _k in '123456789':
    global_keymap[_k] = (
        f'toggle tag hotkey {_k}',
        lambda a, k=_k: a.toggle_tag_hotkey(k))

# ── Local keymaps ────────────────────────────────────────────────────
#
# search_keymap and thread_keymap are empty — all their keys now live
# in global_keymap.

search_keymap: Keymap = {}
"""The local keymap for search panels

All search keys have been consolidated into :data:`global_keymap`.
This dictionary exists so that ``config.py`` can still add
search-specific overrides.
"""

thread_keymap: Keymap = {}
"""The local keymap for thread panels

All thread keys have been consolidated into :data:`global_keymap`.
This dictionary exists so that ``config.py`` can still add
thread-specific overrides.
"""

tag_keymap: Keymap = {
  'j':       ('next tag', lambda p: p.next_tag()),
  'k':       ('previous tag', lambda p: p.previous_tag()),
  '<down>':  ('next tag', lambda p: p.next_tag()),
  '<up>':    ('previous tag', lambda p: p.previous_tag()),
  'g g':     ('first tag', lambda p: p.first_tag()),
  'G':       ('last tag', lambda p: p.last_tag()),
  'C-d':     ('down 20', lambda p: [p.next_tag() for i in range(20)]),
  'C-u':     ('up 20', lambda p: [p.previous_tag() for i in range(20)]),
  '<enter>': ('search tag', lambda p: p.search_current_tag()),
}
"""The local keymap for the tag panel

A dictionary from key strings to pairs consisting of a short docstring and a function
taking :class:`~lazarus.search.TagPanel` as input.
"""

compose_keymap: Keymap = {
  '<escape>':    ('toggle focus', lambda p: p.escape_focus()),
  '<enter>':     ('insert newline', lambda p: p.insert_newline() if hasattr(p, 'insert_newline') else None),
  'H':           ('toggle plaintext', lambda p: p.toggle_plain()),
  'C-s':         ('send', lambda p: p.send()),
  'M-c':         ('reveal Cc', lambda p: p.reveal_cc()),
  'M-b':         ('reveal Bcc', lambda p: p.reveal_bcc()),
  'a':           ('attach file', lambda p: p.attach_file()),
  'e':           ('toggle PGP-encrypt', lambda p: p.toggle_pgp_encrypt()),
  'p':           ('toggle PGP-sign', lambda p: p.toggle_pgp_sign()),
  ']':           ('next SMTP account', lambda p: p.next_account()),
  '[':           ('previous SMTP account', lambda p: p.previous_account()),
}
"""The local keymap for compose panels

A dictionary from key strings to pairs consisting of a short docstring and a function
taking :class:`~lazarus.compose.ComposePanel` as input.
"""

command_bar_keymap: Keymap = {
  '<enter>':  ('accept', lambda b: b.accept()),
  '<escape>': ('close', lambda b: b.close_bar()),
  '<down>':   ('history next', lambda b: b.history_next()),
  '<up>':     ('history previous', lambda b: b.history_previous()),
}
"""The keymap active when the command bar is visible

A dictionary from key strings to pairs consisting of a short docstring and a function
taking :class:`~lazarus.compose.CommandBar` as input. Unlike the other keymaps, the
command bar keymap doesn't accept keychords. Also, you should avoid mapping alphanumeric
keys to commands, as this will interfere with typing.
"""
