#     Dodo - A graphical, hackable email client based on notmuch
#     Copyright (C) 2021 - Aleks Kissinger
#
# This file is part of Dodo
#
# Dodo is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Dodo is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Dodo. If not, see <https://www.gnu.org/licenses/>.

# ── Consolidated global keymap ────────────────────────────────────────
#
# In the split-pane layout the search/dashboard list and the thread
# preview are always visible.  Every key does exactly one thing,
# delegating to the list or the thread preview directly — no key
# changes behaviour depending on focus, and no <escape> preamble is
# needed.
#
# List keys       → Dodo.delegate_to_list()
# Thread keys     → Dodo.delegate_to_thread()
# Global keys     → Dodo methods (open_search, sync_mail, …)

global_keymap = {
  # ── Thread list ──────────────────────────────────────────────────
  'j':           ('next thread', lambda a: a.navigate_list('next')),
  'k':           ('previous thread', lambda a: a.navigate_list('previous')),
  '<down>':      ('next thread', lambda a: a.navigate_list('next')),
  '<up>':        ('previous thread', lambda a: a.navigate_list('previous')),
  '<tab>':       ('next unread', lambda a: a.delegate_to_list('next_thread', unread=True)),
  'S-<tab>':     ('previous unread', lambda a: a.delegate_to_list('previous_thread', unread=True)),
  'g g':         ('first thread', lambda a: a.delegate_to_list('first_thread')),
  'G':           ('last thread', lambda a: a.delegate_to_list('last_thread')),
  'C-d':         ('down 20', lambda a: [a.delegate_to_list('next_thread') for _ in range(20)]),
  'C-u':         ('up 20', lambda a: [a.delegate_to_list('previous_thread') for _ in range(20)]),
  '<pageup>':    ('page up (list)', lambda a: a.delegate_to_list('prev_page')),
  '<pagedown>':  ('page down (list)', lambda a: a.delegate_to_list('next_page')),
  '<enter>':     ('open thread', lambda a: a.delegate_to_list('open_current_thread')),
  'u':           ('toggle unread', lambda a: a.delegate_to_list('toggle_thread_tag', 'unread')),
  'f':           ('toggle flagged', lambda a: a.delegate_to_list('toggle_thread_tag', 'flagged')),
  's':           ('mark and advance', lambda a: a.mark_and_advance()),
  'a':           ('archive', lambda a: a.delegate_to_list('archive_thread')),
  'd':           ('delete', lambda a: a.delegate_to_list('delete_thread')),
  'd d':         ('empty trash', lambda a: a.expunge_trash()),
  'd u':         ('restore from trash', lambda a: a.delegate_to_list('restore_thread_from_trash')),
  'A':           ('archive to local', lambda a: a.delegate_to_list('archive_to_local')),

  # ── Message viewer ───────────────────────────────────────────────
  'J':           ('next message', lambda a: a.delegate_to_thread('next_message')),
  'K':           ('previous message', lambda a: a.delegate_to_thread('previous_message')),
  '<space>':     ('page down (message)', lambda a: a.delegate_to_thread('scroll_message', pages=1)),
  '-':           ('page up (message)', lambda a: a.delegate_to_thread('scroll_message', pages=-1)),
  'H':           ('toggle HTML', lambda a: a.delegate_to_thread('toggle_html')),
  'i':           ('toggle remote images', lambda a: a.delegate_to_thread('toggle_remote_content')),
  'M':           ('toggle thread list mode', lambda a: a.delegate_to_thread('toggle_list_mode')),
  'r':           ('reply to all', lambda a: a.delegate_to_thread('reply', to_all=True)),
  'R':           ('reply', lambda a: a.delegate_to_thread('reply', to_all=False)),
  'C-f':         ('forward', lambda a: a.delegate_to_thread('forward')),
  'O':           ('open attachments', lambda a: a.delegate_to_thread('open_attachments')),
  '<escape>':    ('focus list', lambda a: a.main_window.focus_list()),

  # ── Global ───────────────────────────────────────────────────────
  '?':           ('show help', lambda a: a.show_help()),
  'Q':           ('quit', lambda a: a.prompt_quit()),
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
  'D':           ('show dashboard', lambda a: a.open_dashboard()),
  '/':           ('search', lambda a: a.search_bar()),
  't t':         ('tag', lambda a: a.tag_bar()),
  't m':         ('tag marked', lambda a: a.tag_bar(mode='tag marked')),
}
"""The global keymap

Every key delegates either to the thread list or the thread preview
pane directly (via :func:`~dodo.app.Dodo.delegate_to_list` or
:func:`~dodo.app.Dodo.delegate_to_thread`), so all bindings work
regardless of which pane has keyboard focus.
"""

# Add configurable tag hotkeys (1-9) to the global keymap.
for _k in '123456789':
    global_keymap[_k] = (
        f'toggle tag hotkey {_k}',
        lambda a, k=_k: a.toggle_tag_hotkey(k))

# ── Navigation help (display-only) ───────────────────────────────────

navigation_keymap = {
  'j / k':       ('next / previous thread', lambda a: None),
  'J / K':       ('next / previous message', lambda a: None),
  '<enter>':     ('open thread', lambda a: None),
  '<escape>':    ('focus list', lambda a: None),
  '<space> / -': ('page down / up (message)', lambda a: None),
  's':           ('mark and advance', lambda a: None),
  't m':         ('tag all marked', lambda a: a.tag_bar(mode='tag marked')),
}

# ── Local keymaps ────────────────────────────────────────────────────
#
# search_keymap and thread_keymap are empty — all their keys now live
# in global_keymap.  dashboard_keymap copies search_keymap as before.

search_keymap: dict = {}
"""The local keymap for search panels

All search keys have been consolidated into :data:`global_keymap`.
This dictionary exists so that ``config.py`` can still add
search-specific overrides.
"""

thread_keymap: dict = {}
"""The local keymap for thread panels

All thread keys have been consolidated into :data:`global_keymap`.
This dictionary exists so that ``config.py`` can still add
thread-specific overrides.
"""

dashboard_keymap = dict(search_keymap)
"""The local keymap for the dashboard panel (copy of ``search_keymap``)."""

tag_keymap = {
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
taking :class:`~dodo.search.TagPanel` as input.
"""

compose_keymap = {
  '<enter>': ('edit message', lambda p: p.edit()),
  'S':       ('send', lambda p: p.send()),
  'a':       ('attach file', lambda p: p.attach_file()),
  'e':       ('toggle PGP-encrypt', lambda p: p.toggle_pgp_encrypt()),
  'p':       ('toggle PGP-sign', lambda p: p.toggle_pgp_sign()),
  'w':       ('toggle word wrap', lambda p: p.toggle_wrap()),
  ']':       ('next SMTP account', lambda p: p.next_account()),
  '[':       ('previous SMTP account', lambda p: p.previous_account()),
}
"""The local keymap for compose panels

A dictionary from key strings to pairs consisting of a short docstring and a function
taking :class:`~dodo.compose.ComposePanel` as input.
"""

command_bar_keymap = {
  '<enter>':  ('accept', lambda b: b.accept()),
  '<escape>': ('close', lambda b: b.close_bar()),
  '<down>':   ('history next', lambda b: b.history_next()),
  '<up>':     ('history previous', lambda b: b.history_previous()),
}
"""The keymap active when the command bar is visible

A dictionary from key strings to pairs consisting of a short docstring and a function
taking :class:`~dodo.compose.CommandBar` as input. Unlike the other keymaps, the
command bar keymap doesn't accept keychords. Also, you should avoid mapping alphanumeric
keys to commands, as this will interfere with typing.
"""
