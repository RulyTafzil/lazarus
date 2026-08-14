"""CommandBar — per-mode history, prefill, and the grow-to-content reflow."""
import pytest

from lazarus import mainwindow


@pytest.fixture
def mw(qapp, fake_app, notmuch_stub):
    win = mainwindow.MainWindow(fake_app)
    win.resize(1000, 700)
    win.show()
    return win


@pytest.fixture
def bar(mw):
    return mw.command_bar


def test_open_sets_mode_label(mw, bar):
    bar.open('search', callback=lambda q: None)
    assert bar.label.text() == 'search'
    assert mw.command_area.isVisible()


def test_accept_saves_per_mode_history(bar):
    bar.open('search', callback=lambda q: None)
    bar.setPlainText('tag:inbox')
    bar.accept()
    bar.open('search', callback=lambda q: None)
    bar.setPlainText('tag:flagged')
    bar.accept()
    assert bar.history['search'][1] == ['tag:inbox', 'tag:flagged']


def test_cancel_does_not_save_history(bar):
    bar.open('search', callback=lambda q: None)
    bar.setPlainText('tag:inbox')
    bar.close_bar()
    assert 'search' not in bar.history


def test_history_previous_recalls_and_cursor_at_end(bar):
    bar.open('search', callback=lambda q: None)
    bar.setPlainText('tag:inbox')
    bar.accept()
    bar.open('search', callback=lambda q: None)
    bar.history_previous()
    assert bar.toPlainText() == 'tag:inbox'
    assert bar.textCursor().position() == len('tag:inbox')


def test_escape_closes_bar(mw, bar):
    bar.open('search', callback=lambda q: None)
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent, Qt
    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                   Qt.KeyboardModifier.NoModifier)
    bar.keyPressEvent(ev)
    assert not mw.command_area.isVisible()


def test_overlay_centered(mw, bar, qapp):
    box = mw._command_box
    bar.open('search', callback=lambda q: None)
    g = box.geometry()
    assert abs(g.center().x() - mw.width() / 2) < 3
    assert abs(g.center().y() - mw.height() / 2) < 3


def test_dim_click_dismisses(mw, bar, qapp):
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QPointF, QEvent, Qt
    bar.open('search', callback=lambda q: None)
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    mw.command_area.mousePressEvent(ev)
    assert not mw.command_area.isVisible()


def test_box_padding_click_does_not_dismiss(mw, bar, qapp):
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QPointF, QEvent, Qt
    box = mw._command_box
    bar.open('search', callback=lambda q: None)
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(2, box.height() / 2),
                     QPointF(2, box.height() / 2),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    box.mousePressEvent(ev)
    assert mw.command_area.isVisible()


def test_reflow_grows_with_content(mw, bar, qapp):
    box = mw._command_box
    bar.open('search', callback=lambda q: None)
    base_w = box.width()
    bar.setPlainText('q' * 60)
    assert box.width() > base_w
    assert box.height() == box.height()  # single line


def test_reflow_caps_at_window_width_and_wraps(mw, bar, qapp):
    box = mw._command_box
    max_w = mw.command_area.width() - 48
    bar.open('search', callback=lambda q: None)
    base_h = box.height()
    bar.setPlainText('q' * 200)
    assert box.width() == max_w
    assert box.height() > base_h


def test_reflow_shrinks_back(mw, bar, qapp):
    box = mw._command_box
    bar.open('search', callback=lambda q: None)
    bar.setPlainText('q' * 120)
    wide = box.width()
    bar.setPlainText('q')
    assert box.width() < wide
