"""Compose panel — hidden Cc/Bcc rows, reveal hotkeys, send key."""
import lazarus.settings as settings
from lazarus.compose import ComposePanel
from lazarus import keymap


def _make_panel(qapp, mode='', msg=None, **kw):
    from unittest.mock import MagicMock
    settings.email_address = 'Bob <bob@example.com>'
    p = ComposePanel(MagicMock(), mode, msg, **kw)
    p.resize(600, 500)
    p.show()
    qapp.processEvents()
    return p


def test_cc_and_bcc_rows_hidden_by_default(qapp):
    p = _make_panel(qapp)
    assert not p.cc_row.isVisible()
    assert not p.bcc_row.isVisible()


def test_reveal_cc_shows_and_focuses(qapp):
    p = _make_panel(qapp)
    p.reveal_cc()
    assert p.cc_row.isVisible()
    assert p.cc_field.hasFocus()


def test_reveal_cc_toggles_hide_and_restores(qapp):
    p = _make_panel(qapp)
    p.reveal_cc()
    p.cc_field.setText('carol@example.com')
    p.reveal_cc()                      # hide again
    assert not p.cc_row.isVisible()
    p.reveal_cc()                      # re-reveal
    assert p.cc_row.isVisible()
    assert p.cc_field.text() == 'carol@example.com'  # remembered


def test_hidden_cc_is_disregarded_on_send(qapp):
    p = _make_panel(qapp)
    p.reveal_cc()
    p.cc_field.setText('carol@example.com')
    p.reveal_cc()                      # hide — content must not be sent
    p._sync_data_from_fields()
    assert p._data.cc == []


def test_visible_cc_is_sent(qapp):
    p = _make_panel(qapp)
    p.reveal_cc()
    p.cc_field.setText('carol@example.com')
    p._sync_data_from_fields()
    assert p._data.cc == ['carol@example.com']


def test_hidden_bcc_is_disregarded_on_send(qapp):
    p = _make_panel(qapp)
    p.reveal_bcc()
    p.bcc_field.setText('bob@example.com')
    p.reveal_bcc()
    p._sync_data_from_fields()
    assert p._data.bcc == []


def test_reveal_bcc_shows_and_focuses(qapp):
    p = _make_panel(qapp)
    p.reveal_bcc()
    assert p.bcc_row.isVisible()
    assert p.bcc_field.hasFocus()


def test_reply_all_reveals_populated_cc(qapp):
    msg = {
        'id': 'm1',
        'headers': {
            'Subject': 'S', 'From': 'Alice <alice@example.com>',
            'To': 'Bob <bob@example.com>',
            'Cc': 'Carol <carol@example.com>',
            'Date': 'Thu, 01 Jan 1970 00:00:00 +0000',
        },
        'body': [{'content-type': 'text/plain', 'content': 'hi'}],
        'tags': [], 'crypto': {},
    }
    p = _make_panel(qapp, mode='replyall', msg=msg)
    assert p.cc_row.isVisible()          # reply-all populates Cc -> revealed
    assert 'carol@example.com' in p.cc_field.text()


def test_reply_keeps_cc_hidden_when_empty(qapp):
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
    p = _make_panel(qapp, mode='reply', msg=msg)
    assert not p.cc_row.isVisible()


def test_meta_chords_dispatch_through_panel_keymap(qapp):
    """Alt+C / Alt+B reach the panel keymap (eventFilter routes modifier
    chords from the editor), revealing the rows."""
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent, Qt
    p = _make_panel(qapp)

    ev_c = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_C,
                     Qt.KeyboardModifier.AltModifier)
    p.keyPressEvent(ev_c)
    assert p.cc_row.isVisible()

    ev_b = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_B,
                     Qt.KeyboardModifier.AltModifier)
    p.keyPressEvent(ev_b)
    assert p.bcc_row.isVisible()


def test_send_bound_only_to_cs(qapp):
    assert 'C-s' in keymap.compose_keymap
    assert 'C-S' not in keymap.compose_keymap
    assert 'M-c' in keymap.compose_keymap
    assert 'M-b' in keymap.compose_keymap
    # Ctrl variants stay free for copy/bold in the editor
    assert 'C-c' not in keymap.compose_keymap
    assert 'C-b' not in keymap.compose_keymap
