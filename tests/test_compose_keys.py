"""Compose panel key surface — closed-surface model.

The compose panel is a *closed key surface*: a hotkey may only ever act on
the visible compose panel, or on app-level things that are visible.  Keys
that would delegate to the (hidden) thread list or thread preview — and the
``1-9`` tag hotkeys — are swallowed, in **all** focus states (panel chrome,
editor, and header fields).

Two modes on the compose screen:
  * panel chrome focused  -> compose hotkeys + allowlisted app-level globals
  * editor focused        -> the editor's own shortcuts only (Ctrl+B/I/U,
                             copy/paste, ...) plus the modifier compose
                             chords C-s / M-c / M-b and <escape>, which act
                             on the visible panel; nothing touches hidden
                             panels

<escape> is one-directional: it exits the editor (or a field) to the panel
chrome and never re-enters the editor.

Driven with real QTest.keyClick events so Qt's parent-propagation (the way
unhandled editor/field chords reach the panel) is exercised.
"""
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

import lazarus.settings as settings
from lazarus import keymap
from lazarus.compose import ComposePanel


@pytest.fixture
def panel(qapp):
    settings.email_address = 'Bob <bob@example.com>'
    app = MagicMock()
    p = ComposePanel(app)
    p.resize(600, 500)
    p.show()
    qapp.processEvents()
    # Side-effecting methods don't run — we only observe ROUTING.
    p.send = MagicMock()
    p.attach_file = MagicMock()
    yield p
    # Widget hygiene: close + deleteLater + flush, so a shown top-level
    # panel can't be garbage-collected mid-paint in a later test (which
    # segfaults the shared offscreen QApplication once WebEngine is up).
    p.close()
    p.deleteLater()
    qapp.processEvents()
    from PyQt6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _click(qapp, widget, key, mods=Qt.KeyboardModifier.NoModifier):
    """Give *widget* keyboard focus and send a real key event (with text)."""
    widget.setFocus()
    qapp.processEvents()
    QTest.keyClick(widget, key, mods)
    qapp.processEvents()


# -- allowlist consistency --------------------------------------------------

def test_allowlist_is_subset_of_global_keymap():
    assert keymap.COMPOSE_ALLOWED_GLOBALS <= keymap.global_keymap.keys()


def test_allowlist_excludes_all_list_thread_and_tag_hotkeys():
    """The allowlist may never contain anything that targets the hidden
    thread list or thread preview, or the 1-9 tag hotkeys."""
    forbidden = {
        # list navigation / actions
        'j', 'k', '<down>', '<up>', '<tab>', 'S-<tab>', 'g g', 'G',
        'M-j', 'M-k', '<pageup>', '<pagedown>', '<enter>',
        'u', 'f', 's', 'a', 'A', 'd', 'd d', 'd u',
        # thread / message keys
        'J', 'K', 'M', '<space>', '-', 'H', 'i', 'R', 'r', 'C-y', 'O',
        'C-<enter>', 'C-u', 'C-f', 'C-a', 'C-A', 'C-d', 'C-t',
        # tags / tag hotkeys
        't t', 't m', *(f'{n}' for n in '123456789'),
    }
    assert not (keymap.COMPOSE_ALLOWED_GLOBALS & forbidden)


def test_allowlist_contains_the_full_safe_applevel_set():
    allowed = {
        'C-q', '`', '?', 'C-r', 'c', 'l', 'h', 'x', 'X',
        'I', 'U', 'F', 'T', '/', 'C-/', 't h', 'M-<', 'M->',
    }
    assert keymap.COMPOSE_ALLOWED_GLOBALS == allowed


def test_compose_keys_shadow_blocked_globals():
    """Compose-panel hotkeys that collide with blocked globals are wired
    through compose_keymap, so they act on the visible panel."""
    for k in ('a', 'H', 'p', 'e', '[', ']', 'C-s', 'M-c', 'M-b',
              '<enter>', '<escape>'):
        assert k in keymap.compose_keymap


# -- escape: one-directional -----------------------------------------------

def test_escape_from_editor_focuses_chrome(qapp, panel):
    p = panel
    p.editor.setFocus()
    qapp.processEvents()
    _click(qapp, p.editor, Qt.Key.Key_Escape)
    assert not p.editor.hasFocus()
    assert p.hasFocus()


def test_escape_from_chrome_is_noop(qapp, panel):
    """Escape never re-enters the editor — from the chrome it does nothing."""
    p = panel
    p.setFocus()
    qapp.processEvents()
    _click(qapp, p, Qt.Key.Key_Escape)
    assert p.hasFocus()
    assert not p.editor.hasFocus()


def test_escape_from_field_focuses_chrome(qapp, panel):
    p = panel
    p.to_field.setFocus()
    qapp.processEvents()
    _click(qapp, p.to_field, Qt.Key.Key_Escape)
    assert not p.to_field.hasFocus()
    assert not p.editor.hasFocus()
    assert p.hasFocus()


def _cursor_to_end(p):
    from PyQt6.QtGui import QTextCursor
    c = p.editor.textCursor()
    c.movePosition(QTextCursor.MoveOperation.End)
    p.editor.setTextCursor(c)


def test_enter_from_chrome_inserts_newline_and_focuses_editor(qapp, panel):
    """<enter> with the chrome focused inserts a newline at the editor's
    cursor **and** moves focus into the editor, so typing continues there."""
    p = panel
    p.editor.setPlainText('hello')
    _cursor_to_end(p)
    p.setFocus()
    qapp.processEvents()
    assert not p.editor.hasFocus()
    assert p.hasFocus()
    _click(qapp, p, Qt.Key.Key_Enter)
    assert p.editor.hasFocus()
    assert p.editor.toPlainText() == 'hello\n'


def test_enter_from_editor_stays_in_editor(qapp, panel):
    """<enter> while editing just adds a newline; focus stays put."""
    p = panel
    p.editor.setPlainText('hello')
    _cursor_to_end(p)
    p.editor.setFocus()
    qapp.processEvents()
    _click(qapp, p.editor, Qt.Key.Key_Enter)
    assert p.editor.hasFocus()
    assert p.editor.toPlainText() == 'hello\n'


# -- closed surface: chrome focus ------------------------------------------

def test_chrome_focus_blocked_list_thread_keys_swallowed(qapp, panel):
    """From the panel chrome, list/thread keys must not dispatch anywhere —
    even though they exist in global_keymap."""
    p, app = panel, panel.app
    p.setFocus()
    qapp.processEvents()
    for key, mods in [
        (Qt.Key.Key_J, Qt.KeyboardModifier.NoModifier),      # 'j' next thread
        (Qt.Key.Key_D, Qt.KeyboardModifier.NoModifier),      # 'd' delete
        (Qt.Key.Key_U, Qt.KeyboardModifier.NoModifier),      # 'u' unread
        (Qt.Key.Key_F, Qt.KeyboardModifier.NoModifier),      # 'f' flagged
        (Qt.Key.Key_S, Qt.KeyboardModifier.NoModifier),      # 's' mark/advance
        (Qt.Key.Key_I, Qt.KeyboardModifier.NoModifier),      # 'i' remote images (thread)
        (Qt.Key.Key_K, Qt.KeyboardModifier.ShiftModifier),   # 'K' prev...' up? 'K' prev message
        (Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier),  # '<space>' scroll
        (Qt.Key.Key_Minus, Qt.KeyboardModifier.NoModifier),  # '-' scroll
        (Qt.Key.Key_J, Qt.KeyboardModifier.ShiftModifier),   # 'J' next_message
        (Qt.Key.Key_1, Qt.KeyboardModifier.NoModifier),      # tag hotkey
        (Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier), # 'C-d' delete msg
        (Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier), # 'C-f' flagged msg
        (Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier), # 'C-a' archive msg
        (Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier), # 'C-t' tag msg
        (Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier), # 'C-y' forward
    ]:
        _click(qapp, p, key, mods)
    # Blocked: nothing reaches the list or the thread preview.
    assert app.delegate_to_list.call_count == 0
    assert app.delegate_to_thread.call_count == 0
    assert app.toggle_tag_hotkey.call_count == 0
    assert app.tag_message_bar.call_count == 0
    assert app.forward.call_count == 0
    assert app.mark_and_advance.call_count == 0


def test_chrome_focus_blocked_keychords_swallowed(qapp, panel):
    p, app = panel, panel.app
    p.setFocus()
    qapp.processEvents()
    # 'd d' would empty the trash — must not fire from compose.
    _click(qapp, p, Qt.Key.Key_D)
    _click(qapp, p, Qt.Key.Key_D)
    assert app.expunge_trash.call_count == 0
    # 't t' / 't m' tag bars — blocked; only 't h' theme is allowed.
    _click(qapp, p, Qt.Key.Key_T, Qt.KeyboardModifier.NoModifier)
    _click(qapp, p, Qt.Key.Key_T, Qt.KeyboardModifier.NoModifier)
    assert app.tag_bar.call_count == 0
    _click(qapp, p, Qt.Key.Key_T, Qt.KeyboardModifier.NoModifier)
    _click(qapp, p, Qt.Key.Key_M, Qt.KeyboardModifier.NoModifier)
    assert app.tag_bar.call_count == 0


def test_chrome_focus_allowlisted_globals_fire(qapp, panel):
    p, app = panel, panel.app
    p.setFocus()
    qapp.processEvents()

    _click(qapp, p, Qt.Key.Key_Question)                      # '?' help
    assert app.show_help.called
    _click(qapp, p, Qt.Key.Key_C)                             # 'c' compose
    assert app.open_compose.called
    _click(qapp, p, Qt.Key.Key_L)                             # 'l' next panel
    assert app.next_panel.called
    app.next_panel.reset_mock()
    _click(qapp, p, Qt.Key.Key_H, Qt.KeyboardModifier.NoModifier)  # 'h' prev panel
    assert app.previous_panel.called
    _click(qapp, p, Qt.Key.Key_X, Qt.KeyboardModifier.ShiftModifier)   # 'X' close all
    assert app.close_panel.call_count > 0
    app.close_panel.reset_mock()
    _click(qapp, p, Qt.Key.Key_X)                                      # 'x' close
    assert app.close_panel.called
    _click(qapp, p, Qt.Key.Key_I, Qt.KeyboardModifier.ShiftModifier)   # 'I' inbox
    assert app.open_search.called
    app.open_search.reset_mock()
    _click(qapp, p, Qt.Key.Key_U, Qt.KeyboardModifier.ShiftModifier)   # 'U' unread
    assert app.open_search.called
    _click(qapp, p, Qt.Key.Key_T, Qt.KeyboardModifier.ShiftModifier)   # 'T' tags
    assert app.open_tags.called
    _click(qapp, p, Qt.Key.Key_Slash)                                  # '/' search bar
    assert app.search_bar.called
    _click(qapp, p, Qt.Key.Key_Slash, Qt.KeyboardModifier.ControlModifier)  # 'C-/' edit query
    assert app.edit_search_query.called
    _click(qapp, p, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)  # 'C-r'
    assert app.apply_filter_rules.called
    _click(qapp, p, Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier)  # 'C-q'
    assert app.prompt_quit.called
    _click(qapp, p, Qt.Key.Key_Less, Qt.KeyboardModifier.AltModifier)   # 'M-<'
    assert app.cycle_theme.called

    # 't h' theme bar (chord) — the only allowed 't' chord.
    app.theme_bar.reset_mock()
    _click(qapp, p, Qt.Key.Key_T)
    _click(qapp, p, Qt.Key.Key_H)
    assert app.theme_bar.called


def test_reply_opens_with_editor_focused(qapp):
    """Replies open with the cursor in the body — typing is the immediate
    next action; <escape> exits to the chrome for compose hotkeys."""
    from unittest.mock import MagicMock
    settings.email_address = 'Bob <bob@example.com>'
    msg = {
        'id': 'm1',
        'headers': {
            'Subject': 'S', 'From': 'Alice <alice@example.com>',
            'To': 'Bob <bob@example.com>',
            'Date': 'Thu, 01 Jan 1970 00:00:00 +0000',
        },
        'body': [{'content-type': 'text/plain', 'content': 'hi'}],
        'tags': [], 'crypto': {},
    }
    p = ComposePanel(MagicMock(), mode='reply', msg=msg)
    p.resize(600, 500)
    p.show()
    qapp.processEvents()
    qapp.processEvents()   # deferred focus callback
    assert p.editor.hasFocus()
    p.close()
    p.deleteLater()
    qapp.processEvents()
    from PyQt6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# -- closed surface: editor focus ------------------------------------------

def test_editor_focus_plain_keys_type_not_commands(qapp, panel):
    p, app = panel, panel.app
    p.editor.setFocus()
    qapp.processEvents()
    p.editor.clear()
    _click(qapp, p.editor, Qt.Key.Key_A)          # 'a' -> text, not attach
    assert p.editor.toPlainText() == 'a'
    assert p.attach_file.call_count == 0
    p.editor.clear()
    _click(qapp, p.editor, Qt.Key.Key_J)          # 'j' -> text, not navigaate
    assert p.editor.toPlainText() == 'j'
    assert app.delegate_to_list.call_count == 0
    assert app.navigate_list.call_count == 0


def test_editor_focus_modifier_compose_chords_work(qapp, panel):
    """C-s/M-c/M-b/escape act on the visible panel even while editing."""
    p = panel
    p.editor.setFocus()
    qapp.processEvents()
    _click(qapp, p.editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert p.send.called
    _click(qapp, p.editor, Qt.Key.Key_C, Qt.KeyboardModifier.AltModifier)
    assert p.cc_row.isVisible()


def test_editor_focus_editor_shortcuts_work(qapp, panel):
    p = panel
    p.editor.setFocus()
    qapp.processEvents()
    p.editor.setPlainText('ab')
    p.editor.selectAll()
    _click(qapp, p.editor, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
    assert p.editor.fontWeight() >= 700       # Ctrl+B bold, not global 'b'


def test_editor_focus_leaked_chords_swallowed(qapp, panel):
    """The critical fix: unhandled Ctrl chords no longer leak out of the
    editor to the hidden thread preview."""
    p, app = panel, panel.app
    p.editor.setFocus()
    qapp.processEvents()
    for key in (Qt.Key.Key_D, Qt.Key.Key_F, Qt.Key.Key_T, Qt.Key.Key_A,
                Qt.Key.Key_U, Qt.Key.Key_Y):
        _click(qapp, p.editor, key, Qt.KeyboardModifier.ControlModifier)
    assert app.delegate_to_thread.call_count == 0
    assert app.delete_message.call_count == 0
    assert app.toggle_message_flagged.call_count == 0
    assert app.tag_message_bar.call_count == 0
    assert app.archive_message.call_count == 0
    assert app.forward.call_count == 0


def test_editor_focus_allowlisted_chords_still_fire(qapp, panel):
    """App-level chords C-r / C-q leak through to allowed globals."""
    p, app = panel, panel.app
    p.editor.setFocus()
    qapp.processEvents()
    _click(qapp, p.editor, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    assert app.apply_filter_rules.called
    _click(qapp, p.editor, Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier)
    assert app.prompt_quit.called


def test_editor_focus_h_is_text_not_plaintext(qapp, panel):
    """Shift+H composes a letter in the editor; plaintext toggle is a
    chrome-mode hotkey."""
    p = panel
    p.editor.setFocus()
    qapp.processEvents()
    p.editor.setPlainText('')
    _click(qapp, p.editor, Qt.Key.Key_H, Qt.KeyboardModifier.ShiftModifier)
    assert not p.editor.plain_mode


# -- closed surface: field focus -------------------------------------------

def test_field_focus_plain_keys_type(qapp, panel):
    p = panel
    p.to_field.setFocus()
    qapp.processEvents()
    p.to_field.clear()
    _click(qapp, p.to_field, Qt.Key.Key_A)
    assert p.to_field.text() == 'a'
    assert p.attach_file.call_count == 0


def test_field_focus_leaked_chords_swallowed(qapp, panel):
    p, app = panel, panel.app
    p.to_field.setFocus()
    qapp.processEvents()
    _click(qapp, p.to_field, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    _click(qapp, p.to_field, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert app.delegate_to_thread.call_count == 0


def test_field_focus_cs_sends_and_allowlisted_chords_fire(qapp, panel):
    p, app = panel, panel.app
    p.to_field.setFocus()
    qapp.processEvents()
    _click(qapp, p.to_field, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert p.send.called
    _click(qapp, p.to_field, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    assert app.apply_filter_rules.called
