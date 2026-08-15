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


def _make_panel_with_address(qapp, email_address):
    """ComposePanel with a pre-set email_address (combo items are fixed
    at construction, so the address must be set before building)."""
    from unittest.mock import MagicMock
    settings.email_address = email_address
    p = ComposePanel(MagicMock())
    p.resize(600, 500)
    p.show()
    qapp.processEvents()
    return p


def test_cc_and_bcc_rows_hidden_by_default(qapp):
    p = _make_panel(qapp)
    assert not p.cc_row.isVisible()
    assert not p.bcc_row.isVisible()


def _row_label(field) -> str:
    """Text of the QLabel sitting left of *field* in its row layout."""
    from PyQt6.QtWidgets import QLabel
    row = field.parentWidget()
    assert row is not None
    lay = row.layout()
    assert lay is not None
    for i in range(lay.count()):
        w = lay.itemAt(i).widget()
        if isinstance(w, QLabel):
            return w.text()
    return ''


def test_fields_have_left_labels(qapp):
    """To/Cc/Bcc/Subject/From show real labels left of the inputs."""
    p = _make_panel(qapp)
    assert _row_label(p.to_field) == 'To:'
    assert _row_label(p.subject_field) == 'Subject:'
    assert _row_label(p.cc_field) == 'Cc:'
    assert _row_label(p.bcc_field) == 'Bcc:'
    assert _row_label(p.from_combo) == 'From:'
    # Placeholders are redundant now that labels exist.
    assert p.to_field.placeholderText() == ''
    assert p.subject_field.placeholderText() == ''


def test_field_right_edges_align_with_from(qapp):
    """To/Cc/Bcc/Subject boxes end at the same x as the From dropdown,
    which reserves trailing space for the PGP/send status label."""
    p = _make_panel(qapp)
    p.resize(900, 600)
    p.show()
    p.cc_row.show()
    p.bcc_row.show()
    qapp.processEvents()
    from_right = p.from_combo.geometry().right()
    for field in (p.to_field, p.subject_field, p.cc_field, p.bcc_field):
        assert field.geometry().right() == from_right, field


def test_from_row_has_top_padding(qapp):
    """The From row (first in the layout) sits ~4px below the panel top,
    matching the inter-field spacing."""
    from PyQt6.QtCore import QPoint
    p = _make_panel(qapp)
    p.resize(900, 600)
    p.show()
    qapp.processEvents()
    pos = p.from_combo.mapTo(p, QPoint(0, 0))
    assert pos.y() >= 4


def test_field_font_smaller_than_body(qapp):
    """Labels + field text render one point smaller than the body."""
    p = _make_panel(qapp)
    assert p._field_font_size == max(settings.message_font_size - 1, 8)


def test_toolbar_compact_and_left_aligned(qapp):
    """The format strip hugs its content and sits at the left edge."""
    p = _make_panel(qapp)
    p.resize(900, 600)
    p.show()
    qapp.processEvents()
    assert p.format_bar.width() < p.width()
    assert p.format_bar.geometry().left() < 10


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


def test_h_toggles_plaintext_mode(qapp):
    """Shift+H (compose chrome focus) mirrors the reading view's HTML
    toggle — plain ``h`` stays the panel switch."""
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    from lazarus import util
    assert 'H' in keymap.compose_keymap
    p = _make_panel(qapp)
    assert not p.editor.plain_mode
    assert 'Plain' not in p.status_label.text()

    # plain h -> 'h' (previous panel), not the toggle
    ev_h = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_H,
                     Qt.KeyboardModifier.NoModifier)
    assert util.key_string(ev_h) == 'h'
    p.keyPressEvent(ev_h)
    assert not p.editor.plain_mode

    # Shift+H -> 'H' -> plaintext toggle
    ev_H = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_H,
                     Qt.KeyboardModifier.ShiftModifier)
    assert util.key_string(ev_H) == 'H'
    p.keyPressEvent(ev_H)
    assert p.editor.plain_mode
    assert 'Plain' in p.status_label.text()

    p.keyPressEvent(ev_H)
    assert not p.editor.plain_mode
    assert 'Plain' not in p.status_label.text()


def test_plaintext_send_builds_plain_only_message(qapp):
    """In plaintext mode the outgoing message has no HTML part."""
    from lazarus.mime_builder import build_message
    p = _make_panel(qapp)
    p.editor.setPlainText('just text')
    p.toggle_plain()
    p._sync_data_from_fields()
    assert p._data.body_html == ''
    assert p._data.body_text == 'just text'
    eml = build_message(p._data)
    assert 'multipart' not in eml.get_content_type()
    assert eml.get_content_type() == 'text/plain'


def test_account_cycle_wraps_and_updates_from(qapp):
    """[ / ] cycle through smtp_accounts with wrap-around, and the From
    dropdown selection follows the current account."""
    settings.smtp_accounts = ['a', 'b', 'c']
    settings.use_signature = False
    # Set the per-account dict BEFORE building the panel: the combo's
    # items are fixed at construction time.
    settings.email_address = {
        'a': 'A <a@example.com>',
        'b': 'B <b@example.com>',
        'c': 'C <c@example.com>',
    }
    p = _make_panel_with_address(qapp, settings.email_address)
    assert p.current_account == 0
    assert p.from_combo.currentIndex() == 0
    assert 'a@example.com' in p.from_combo.currentText()

    p.next_account()
    assert p.current_account == 1
    assert p.from_combo.currentIndex() == 1
    assert 'b@example.com' in p.from_combo.currentText()
    p.next_account()
    assert p.current_account == 2
    assert p.from_combo.currentIndex() == 2
    p.next_account()  # wraps to 0
    assert p.current_account == 0
    assert p.from_combo.currentIndex() == 0
    p.previous_account()  # wraps to 2
    assert p.current_account == 2
    assert p.from_combo.currentIndex() == 2


def test_from_combo_switches_account(qapp):
    """Picking an item in the From dropdown switches account (the mouse
    path, mirroring [ / ])."""
    settings.smtp_accounts = ['a', 'b']
    settings.use_signature = False
    settings.email_address = {
        'a': 'A <a@example.com>',
        'b': 'B <b@example.com>',
    }
    p = _make_panel_with_address(qapp, settings.email_address)
    assert p.from_combo.currentText() == 'A <a@example.com>'

    p.from_combo.setCurrentIndex(1)
    assert p.current_account == 1
    assert p.from_combo.currentText() == 'B <b@example.com>'
    assert 'b@example.com' in p._data.from_addr

    p.from_combo.setCurrentIndex(0)
    assert p.current_account == 0
    assert 'a@example.com' in p._data.from_addr


def test_from_combo_prefixes_duplicate_addresses(qapp):
    """When accounts share one address (plain-string config), the account
    name prefixes the dropdown text so choices stay distinguishable."""
    settings.smtp_accounts = ['gmail', 'work']
    settings.use_signature = False
    p = _make_panel_with_address(
        qapp, 'Same <s@example.com>')  # same for every account
    items = [p.from_combo.itemText(i)
             for i in range(p.from_combo.count())]
    assert items[0].startswith('gmail ·')
    assert items[1].startswith('work ·')
