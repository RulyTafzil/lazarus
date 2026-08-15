"""RichTextEditor formatting toolbar — buttons, sync, formatting ops."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor, QTextListFormat

from lazarus.compose import ComposePanel


def _panel(qapp):
    from unittest.mock import MagicMock
    import lazarus.settings as settings
    settings.email_address = 'Bob <bob@example.com>'
    p = ComposePanel(MagicMock())
    p.resize(600, 500)
    p.show()
    qapp.processEvents()
    return p


def test_toolbar_built_with_expected_buttons(qapp):
    p = _panel(qapp)
    ed = p.editor
    assert p.format_bar is not None
    for name in ('bold', 'italic', 'underline', 'bullet', 'numbered'):
        assert ed._fmt_buttons[name].isCheckable()
    # alignment buttons are checkable + exclusive
    assert len(ed._align_buttons) == 3
    assert all(b.isCheckable() for b, _ in ed._align_buttons)
    # colour / image buttons are plain actions, not toggles
    assert not ed._color_btn.isCheckable()
    assert not ed._image_btn.isCheckable()
    # no button steals keyboard focus from the editor
    for b, _ in ed._align_buttons:
        assert b.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_bold_toggle_syncs_button(qapp):
    p = _panel(qapp)
    ed = p.editor
    ed.setPlainText('hello world')
    cursor = ed.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    ed.setTextCursor(cursor)

    ed.toggle_bold()
    assert ed.fontWeight() >= 700
    assert ed._fmt_buttons['bold'].isChecked()
    ed.toggle_bold()
    assert ed.fontWeight() < 700
    assert not ed._fmt_buttons['bold'].isChecked()


def test_alignment_exclusive_group(qapp):
    p = _panel(qapp)
    ed = p.editor
    ed._set_alignment(Qt.AlignmentFlag.AlignHCenter)
    assert ed.alignment() & Qt.AlignmentFlag.AlignHCenter
    checked = [flag for _b, flag in ed._align_buttons if _b.isChecked()]
    assert len(checked) == 1
    assert checked[0] == Qt.AlignmentFlag.AlignHCenter


def test_list_toggle_on_and_off(qapp):
    p = _panel(qapp)
    ed = p.editor
    ed.setPlainText('item one\nitem two')
    ed.moveCursor(QTextCursor.MoveOperation.Start)

    ed._toggle_list(QTextListFormat.Style.ListDisc)
    ed._sync_format_buttons()
    assert ed.textCursor().currentList() is not None
    assert ed._fmt_buttons['bullet'].isChecked()
    assert not ed._fmt_buttons['numbered'].isChecked()

    ed._toggle_list(QTextListFormat.Style.ListDisc)
    ed._sync_format_buttons()
    assert ed.textCursor().currentList() is None
    assert not ed._fmt_buttons['bullet'].isChecked()


def test_plain_toggle_strips_formatting_and_kills_html(qapp):
    p = _panel(qapp)
    ed = p.editor
    ed.setPlainText('hello')
    cursor = ed.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    ed.setTextCursor(cursor)
    ed.toggle_bold()
    assert ed.fontWeight() >= 700
    assert ed.body_html()  # rich mode produces HTML

    assert ed.toggle_plain() is True
    assert ed.plain_mode
    assert ed.body_html() == ''
    assert ed.toPlainText() == 'hello'  # text preserved
    assert ed.fontWeight() < 700  # formatting stripped
    assert ed.collect_inline_images() == {}  # no-op, no HTML rewrite

    assert ed.toggle_plain() is False
    assert ed.body_html()  # back to rich


def test_plain_mode_disables_format_buttons_and_swallows_ctrl_b(qapp):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    p = _panel(qapp)
    ed = p.editor
    ed.toggle_plain()
    assert ed._plain_btn.isChecked()
    assert all(not b.isEnabled() for b in ed._format_buttons)

    # Ctrl+B is swallowed while plain (no formatting to apply)
    ed.setPlainText('x')
    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_B,
                   Qt.KeyboardModifier.ControlModifier)
    ed.keyPressEvent(ev)
    assert ed.fontWeight() < 700

    ed.toggle_plain()
    assert all(b.isEnabled() for b in ed._format_buttons)
    assert not ed._plain_btn.isChecked()


def test_segmented_toggle_switches_mode(qapp):
    """Clicking the Plaintext/HTML segments switches compose mode and
    the segments follow the actual state (incl. the H key path)."""
    p = _panel(qapp)
    ed = p.editor
    assert not ed.plain_mode
    assert ed._html_btn.isChecked()
    assert not ed._plain_btn.isChecked()

    # click the Plaintext segment
    ed._plain_btn.click()
    assert ed.plain_mode
    assert ed._plain_btn.isChecked()
    assert not ed._html_btn.isChecked()
    assert all(not b.isEnabled() for b in ed._format_buttons)

    # toggle via the H-key path — the segments must follow
    ed.toggle_plain()
    assert not ed.plain_mode
    assert ed._html_btn.isChecked()
    assert not ed._plain_btn.isChecked()
    assert all(b.isEnabled() for b in ed._format_buttons)

    # click the HTML segment (already rich: no-op)
    ed._html_btn.click()
    assert not ed.plain_mode

    # back to plain via segment
    ed._plain_btn.click()
    assert ed.plain_mode
    ed._html_btn.click()
    assert not ed.plain_mode


def test_segmented_toggle_is_leftmost(qapp):
    """Plaintext and HTML sit at the far left of the toolbar, styled
    like the buttons beside them (no container box)."""
    from PyQt6.QtWidgets import QFrame
    p = _panel(qapp)
    bar_lay = p.format_bar.layout()
    assert bar_lay is not None
    first = bar_lay.itemAt(0).widget()
    second = bar_lay.itemAt(1).widget()
    third = bar_lay.itemAt(2).widget()
    assert first is p.editor._plain_btn
    assert second is p.editor._html_btn
    assert isinstance(third, QFrame)  # separator after the toggle


def test_segment_buttons_match_toolbar_style(qapp):
    """Unselected segments are plain text on the background; the active
    one gets the checked (darker) fill like the other toolbar buttons."""
    p = _panel(qapp)
    ed = p.editor
    assert not ed._plain_btn.isChecked()
    assert ed._html_btn.isChecked()  # HTML active by default
    assert ed._plain_btn.styleSheet() == ed._fmt_buttons['bold'].styleSheet()
    ed._plain_btn.click()
    assert ed._plain_btn.isChecked()
    assert not ed._html_btn.isChecked()
