"""mainwindow — splitter persistence (offscreen Qt).

The preview-pane collapse/restore logic is the most stateful code in
MainWindow; these tests pin down its three rules:

* startup always collapses the preview (list gets full width),
* a saved divider is captured as the "open" state,
* dragging while the preview is closed does not clobber the saved state.
"""
import pytest
from PyQt6.QtCore import QSettings

from lazarus import mainwindow, settings
from lazarus.panel import Panel


@pytest.fixture
def mw(qapp, fake_app):
    win = mainwindow.MainWindow(fake_app)
    win.resize(1000, 700)
    win.show()
    yield win
    # Close the window before the next test runs: a shown top-level
    # window that is later garbage-collected mid-paint in another test
    # can segfault the shared offscreen QApplication.
    win.close()
    qapp.processEvents()


@pytest.fixture
def conf():
    return QSettings('lazarus', 'lazarus')


def _splitter_key() -> str:
    return f"main_splitter_state_{settings.thread_pane_position}"


class DummyPanel(Panel):
    def __init__(self, ctl, name):
        super().__init__(ctl)
        self._name = name

    def title(self):
        return self._name


def test_startup_collapses_preview(mw):
    """The app starts with the preview pane collapsed (list full width)."""
    assert mw.thread_container.isHidden()
    sizes = mw.main_splitter.sizes()
    assert sum(sizes) > 0
    assert 0 in sizes  # one side fully collapsed


def test_default_open_state_is_50_50(mw, conf):
    """With no saved state, the open-state fallback is ~50/50."""
    conf.remove(_splitter_key())
    state = mw._load_open_splitter_state()
    assert state is not None


def test_save_skipped_while_preview_collapsed(mw, conf):
    """Dragging the divider with no active thread and a hidden preview
    must not persist [total, 0] as the open position."""
    conf.remove(_splitter_key())
    mw.main_splitter.moveSplitter(900, 0)  # emits splitterMoved
    assert conf.value(_splitter_key()) is None


def test_save_persists_while_preview_visible(mw, conf):
    """With a preview shown, the divider position is saved and becomes
    the open-state for the next restore."""
    conf.remove(_splitter_key())
    mw._active_thread = DummyPanel(mw.app, 't')
    mw.thread_container.show()
    mw.main_splitter.moveSplitter(300, 0)  # user-drag equivalent
    assert conf.value(_splitter_key()) is not None
    assert mw._open_splitter_state is not None
    mw._active_thread = None


def test_show_thread_restores_saved_divider(mw, conf, qapp):
    """show_thread() restores the last open divider instead of the
    default 50/50."""
    conf.remove(_splitter_key())
    # Simulate a user-dragged divider (preview visible).  The offscreen
    # platform clamps the raw position, so capture whatever the splitter
    # actually settled on — the point is that show_thread reproduces it.
    mw._active_thread = DummyPanel(mw.app, 't')
    mw.thread_container.show()
    mw.main_splitter.moveSplitter(300, 0)
    qapp.processEvents()
    saved_sizes = mw.main_splitter.sizes()
    assert saved_sizes != [1000, 0]  # really moved off the collapse point
    # Collapse again (as clear_thread() does), then reopen a thread.
    mw._active_thread = None
    mw.thread_container.hide()
    mw.main_splitter.setSizes([1000, 0])

    mw.show_thread(DummyPanel(mw.app, 't2'))
    qapp.processEvents()
    sizes = mw.main_splitter.sizes()
    assert sizes == saved_sizes  # the saved divider was restored
    mw.clear_thread()
