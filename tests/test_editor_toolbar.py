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
