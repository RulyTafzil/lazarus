"""AppController — panel orchestration, dispatch, tag bar (offscreen Qt)."""
import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from lazarus import mainwindow
from lazarus.controller import AppController
from lazarus.panel import Panel
from lazarus.search import SearchPanel
from tests.conftest import make_thread


@pytest.fixture
def mw(qapp, fake_app, notmuch_stub):
    win = mainwindow.MainWindow(fake_app)
    win.resize(1000, 700)
    win.show()
    return win


@pytest.fixture
def ctl(mw, fake_app):
    return AppController(fake_app, mw)  # type: ignore[arg-type]


class DummyPanel(Panel):
    def __init__(self, ctl, name):
        super().__init__(ctl)
        self._name = name

    def title(self):
        return self._name


def test_close_panel_by_index_zero(ctl, mw, qapp):
    """close_panel(0) closes tab 0, not the current tab."""
    for name in ('a', 'b', 'c'):
        ctl.add_panel(DummyPanel(ctl, name))
    assert mw.tabs.count() == 3
    mw.tabs.setCurrentWidget(mw.tabs.widget(2))
    ctl.close_panel(0)
    names = [mw.tabs.tabText(i) for i in range(mw.tabs.count())]
    assert names == ['b', 'c']


def test_close_panel_none_closes_current(ctl, mw, qapp):
    for name in ('a', 'b'):
        ctl.add_panel(DummyPanel(ctl, name))
    mw.tabs.setCurrentIndex(0)
    ctl.close_panel()
    assert mw.tabs.count() == 1


def test_close_panel_by_widget(ctl, mw, qapp):
    a = DummyPanel(ctl, 'a')
    ctl.add_panel(a)
    ctl.close_panel(a)
    assert mw.tabs.count() == 0


def _flush_deferred_deletes() -> None:
    """Deliver pending DeferredDelete events.

    deleteLater() posts a DeferredDelete event; processEvents() alone
    does not flush those (the real event loop does, continuously)."""
    from PyQt6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_close_panel_deletes_widget(ctl, mw, qapp):
    """Closed tab panels are destroyed, not just detached from the tab
    widget — removeTab alone leaked every closed panel for the session."""
    from PyQt6 import sip
    a = DummyPanel(ctl, 'a')
    ctl.add_panel(a)
    ctl.close_panel(a)
    _flush_deferred_deletes()
    assert sip.isdeleted(a)


def test_close_panel_skips_delete_while_sending(ctl, mw, qapp):
    """A ComposePanel with an in-flight SendmailThread must survive
    close (deleting it would kill the running QThread); compose's send
    completion callback performs the deleteLater instead."""
    from PyQt6 import sip
    a = DummyPanel(ctl, 'a')
    a.sendmail_thread = object()  # in-flight send marker
    ctl.add_panel(a)
    ctl.close_panel(a)
    _flush_deferred_deletes()
    assert mw.tabs.count() == 0          # still removed from the tabs
    assert not sip.isdeleted(a)          # …but not destroyed
    del a.sendmail_thread


def test_open_search_dedupes(ctl, mw, qapp, notmuch_stub):
    notmuch_stub.threads = [make_thread('t1', 'Hello')]
    ctl.open_search('tag:inbox')
    ctl.open_search('tag:inbox')
    # same query -> same tab, not a duplicate
    assert ctl.num_panels() == 1


def test_refresh_tab_titles_batches_counts(ctl, mw, qapp, notmuch_stub):
    """Dirty search tabs share one ``count --batch`` invocation instead
    of one subprocess per tab."""
    notmuch_stub.threads = [
        make_thread('t1', 'A'), make_thread('t2', 'B')]  # both tag:inbox
    ctl.open_search('tag:inbox')
    ctl.open_search('tag:flagged')
    # Dirty both titles (as refresh_panels does for non-current tabs).
    for i in range(mw.tabs.count()):
        mw.tabs.widget(i).dirty = True

    batch_before = sum(1 for c in notmuch_stub.count_calls
                       if c[0] == '__batch__')
    ctl.refresh_tab_titles()
    batch_after = sum(1 for c in notmuch_stub.count_calls
                      if c[0] == '__batch__')

    assert batch_after == batch_before + 1       # one batch for both tabs
    assert mw.tabs.tabText(0) == 'tag:inbox [2]'
    assert mw.tabs.tabText(1) == 'tag:flagged [0]'


def test_navigate_list_on_search_panel(ctl, mw, qapp, notmuch_stub):
    notmuch_stub.threads = [make_thread('t1', 'A'), make_thread('t2', 'B')]
    ctl.open_search('tag:inbox')
    sp = mw.tabs.currentWidget()
    ctl.navigate_list('next')
    assert sp.tree.currentIndex().row() == 1
    ctl.navigate_list('previous')
    assert sp.tree.currentIndex().row() == 0


def test_delegate_to_list_unknown_method_warns(ctl, mw, qapp, notmuch_stub, caplog):
    notmuch_stub.threads = [make_thread('t1', 'A')]
    ctl.open_search('tag:inbox')
    import logging
    with caplog.at_level(logging.WARNING):
        ctl.delegate_to_list('no_such_method')
    assert 'unknown method' in caplog.text


def test_tag_bar_prefills_plus(ctl, qapp):
    ctl.tag_bar('tag')
    bar = ctl.command_bar
    assert bar.toPlainText() == '+'
    assert bar.textCursor().position() == 1


def test_tag_bar_empty_expr_is_noop(ctl, mw, qapp, notmuch_stub):
    notmuch_stub.threads = [make_thread('t1', 'A')]
    ctl.open_search('tag:inbox')
    ctl.tag_bar('tag')
    ctl.command_bar.accept()
    assert notmuch_stub.tag_calls == []


def test_tag_bar_typed_expr_dispatches(ctl, mw, qapp, notmuch_stub):
    notmuch_stub.threads = [make_thread('t1', 'A')]
    ctl.open_search('tag:inbox')
    ctl.tag_bar('tag')
    ctl.command_bar.setPlainText('+work')
    ctl.command_bar.accept()
    assert len(notmuch_stub.tag_calls) == 1
    assert notmuch_stub.tag_calls[0][0] == '+work'


def test_mark_and_advance(ctl, mw, qapp, notmuch_stub):
    notmuch_stub.threads = [make_thread('t1', 'A'), make_thread('t2', 'B')]
    ctl.open_search('tag:inbox')
    ctl.mark_and_advance()
    assert notmuch_stub.tag_calls[0][0] == '+marked'
    assert mw.tabs.currentWidget().tree.currentIndex().row() == 1


# -- tag_message_bar (C-t) -------------------------------------------------

class FakeThreadPanel(Panel):
    """A real Panel that satisfies the ThreadView protocol structurally,
    so the controller's isinstance gate passes without QWebEngine."""

    def __init__(self, ctl):
        super().__init__(ctl)
        self.tag_calls: list[str] = []
        self.reply_calls: list[bool] = []

    def tag_message(self, tag_expr: str) -> None:
        self.tag_calls.append(tag_expr)

    def reply(self, to_all: bool = True) -> None:
        self.reply_calls.append(to_all)

    def forward(self) -> None:
        self.reply_calls.append(None)

    def next_message(self) -> None:
        pass

    def previous_message(self) -> None:
        pass

    def scroll_message(self, *a, **k) -> None:
        pass

    def toggle_html(self) -> None:
        pass

    def toggle_remote_content(self) -> None:
        pass

    def toggle_list_mode(self) -> None:
        pass

    def open_attachments(self) -> None:
        pass

    def toggle_message_unread(self) -> None:
        pass

    def toggle_message_flagged(self) -> None:
        pass

    def archive_message(self) -> None:
        pass

    def archive_message_to_local(self) -> None:
        pass

    def delete_message(self) -> None:
        pass

    def update_thread(self, thread_id: str, msg_id: str | None = None) -> None:
        pass


def test_tag_message_bar_prefills_and_dispatches(ctl, mw, qapp):
    fake_thread = FakeThreadPanel(ctl)
    mw._active_thread = fake_thread
    ctl.tag_message_bar()
    assert ctl.command_bar.toPlainText() == '+'
    assert ctl.command_bar.label.text() == 'tag message'
    ctl.command_bar.setPlainText('+work')
    ctl.command_bar.accept()
    assert fake_thread.tag_calls == ['+work']


def test_tag_message_bar_empty_expr_is_noop(ctl, mw, qapp):
    fake_thread = FakeThreadPanel(ctl)
    mw._active_thread = fake_thread
    ctl.tag_message_bar()
    ctl.command_bar.accept()  # bare '+' only
    assert fake_thread.tag_calls == []


def test_tag_message_bar_without_thread_noop(ctl, mw, qapp):
    mw._active_thread = None
    ctl.tag_message_bar()
    assert not mw.command_area.isVisible()  # bar never opened
    assert ctl.command_bar.callback is None


# --- theme switching -----------------------------------------------------
#
# These deliberately do NOT use the `mw`/`ctl` fixtures (a full
# MainWindow, which always embeds a QWebEngineView-backed preview pane).
# Constructing that repeatedly, on top of everything else the suite
# already does, has been observed to destabilize the offscreen Qt/
# WebEngine session when run as part of the full suite. set_theme /
# cycle_theme / theme_bar only ever touch `self.tabs` (a QTabWidget) and
# `self.command_bar` (a QPlainTextEdit) -- neither needs real window
# chrome, so build those directly instead.

@pytest.fixture
def theme_ctl(qapp, monkeypatch):
    """A real AppController wired to a standalone tab widget + command
    bar (no MainWindow/QWebEngineView), plus an isolated fake theme
    registry so tests don't depend on -- or mutate -- the real one."""
    from lazarus import mainwindow, commandbar, themes as themes_mod
    import lazarus.settings as settings

    fake_registry = {
        'nord': themes_mod.nord,
        'gruvbox_dark': themes_mod.gruvbox_dark,
        'Dracula': themes_mod.terminal_theme_to_lazarus({
            'name': 'Dracula', 'background': '#282a36', 'foreground': '#f8f8f2',
            'palette': {str(i): '#000000' for i in range(16)},
        }),
    }
    monkeypatch.setattr(themes_mod, 'REGISTRY', fake_registry)
    monkeypatch.setattr(themes_mod, '_current_name', 'nord')
    monkeypatch.setattr(themes_mod, '_save_last_theme_name', lambda name: None)
    monkeypatch.setattr(themes_mod, 'load_last_theme_name', lambda: None)
    monkeypatch.setattr(settings, 'theme', themes_mod.nord)

    # apply_theme() mutates the *real*, process-wide QApplication's
    # palette/stylesheet -- side effects that outlive this test and
    # pollute every later test in the same session (already verified
    # visually elsewhere in this project's history). These tests only
    # care about set_theme's bookkeeping (settings.theme rebind,
    # current_name, persistence call), so stub it out.
    monkeypatch.setattr(themes_mod, 'apply_theme', lambda theme: None)

    tabs = mainwindow.WatermarkTabWidget()
    label = QLabel()
    bar = commandbar.CommandBar(None, label, tabs)  # type: ignore[arg-type]

    class FakeMainWindow:
        def __init__(self):
            self.tabs = tabs
            self.command_bar = bar
        def active_thread(self):
            return None

    main_window = FakeMainWindow()
    bar.app = main_window  # CommandBar.close_bar() reads self.app.tabs

    class FakeApp:
        panel_history: list = []

    ctl = AppController.__new__(AppController)
    from PyQt6.QtCore import QObject
    QObject.__init__(ctl, None)
    ctl.app = FakeApp()
    ctl.main_window = main_window  # type: ignore[assignment]
    ctl.tabs = tabs
    ctl.command_bar = bar
    ctl.panel_history = []
    ctl.num_panels = lambda: 0

    statuses: list[tuple[str, str]] = []
    ctl.status_message = lambda msg, kind='info', duration=3000: statuses.append((msg, kind))  # type: ignore[method-assign]
    ctl._statuses = statuses  # type: ignore[attr-defined]

    return ctl


def test_set_theme_applies_and_updates_settings(theme_ctl, qapp):
    import lazarus.settings as settings
    from lazarus import themes as themes_mod
    theme_ctl.set_theme('Dracula')
    assert settings.theme is themes_mod.REGISTRY['Dracula']
    assert themes_mod.current_name() == 'Dracula'


def test_set_theme_invalidates_tab_mesh(theme_ctl, qapp):
    theme_ctl.tabs.resize(400, 30)
    theme_ctl.tabs.show()
    qapp.processEvents()
    theme_ctl.tabs._get_mesh(theme_ctl.tabs.rect())  # force the cache to populate
    assert theme_ctl.tabs._mesh_cache is not None
    theme_ctl.set_theme('Dracula')
    assert theme_ctl.tabs._mesh_cache is None


def test_set_theme_unknown_name_shows_error_status(theme_ctl, qapp):
    import lazarus.settings as settings
    before = settings.theme
    theme_ctl.set_theme('Not A Real Theme')
    assert settings.theme is before  # unchanged
    assert theme_ctl._statuses[-1][1] == 'error'


def test_set_theme_empty_name_is_noop(theme_ctl, qapp):
    import lazarus.settings as settings
    before = settings.theme
    theme_ctl.set_theme('')
    assert settings.theme is before
    assert theme_ctl._statuses == []


def test_cycle_theme_next_and_previous(theme_ctl, qapp):
    from lazarus import themes as themes_mod
    names = themes_mod.ordered_names()
    theme_ctl.set_theme(names[0])
    theme_ctl.cycle_theme(1)
    assert themes_mod.current_name() == names[1]
    theme_ctl.cycle_theme(-1)
    assert themes_mod.current_name() == names[0]


def test_cycle_theme_wraps_around(theme_ctl, qapp):
    from lazarus import themes as themes_mod
    names = themes_mod.ordered_names()
    theme_ctl.set_theme(names[-1])
    theme_ctl.cycle_theme(1)
    assert themes_mod.current_name() == names[0]


def test_theme_bar_opens_with_theme_mode(theme_ctl, qapp):
    theme_ctl.theme_bar()
    assert theme_ctl.command_bar.label.text() == 'theme'
    # 'theme:' is prefilled so the user just types the name
    assert theme_ctl.command_bar.toPlainText() == 'theme:'
    # cursor sits after the prefill, ready to type the name
    assert theme_ctl.command_bar.textCursor().position() == len('theme:')


def test_theme_bar_accepts_theme_colon_syntax(theme_ctl, qapp):
    """theme:Name form — the canonical 'theme:[themename]' input."""
    from lazarus import themes as themes_mod
    theme_ctl.theme_bar()
    callback = theme_ctl.command_bar.callback
    assert callback is not None
    callback('theme:Dracula')
    assert themes_mod.current_name() == 'Dracula'
    callback('theme:  nord  ')
    assert themes_mod.current_name() == 'nord'
    callback('theme:')
    # empty name is a no-op, not an error
    assert themes_mod.current_name() == 'nord'


def test_theme_bar_strips_whitespace_and_applies(theme_ctl, qapp):
    """Exercises exactly what theme_bar() wires up (a callback that
    strips whitespace and calls set_theme) directly, rather than going
    through CommandBar.accept()'s full completer-popup/focus machinery
    -- which assumes the normal MainWindow-driven construction path
    this lightweight fixture deliberately skips."""
    from lazarus import themes as themes_mod
    theme_ctl.theme_bar()
    callback = theme_ctl.command_bar.callback
    assert callback is not None
    callback('  Dracula  ')
    assert themes_mod.current_name() == 'Dracula'


def test_theme_completion_strips_theme_colon_prefix(theme_ctl, qapp):
    """Autocomplete on 'theme:dra' suggests theme names, like 'tag:'
    suggests tags: the prefix after 'theme:' drives the completer."""
    bar = theme_ctl.command_bar
    bar.open('theme', lambda t: None)
    bar.setPlainText('theme:dra')
    assert bar.completer.completionPrefix() == 'dra'
    assert bar.completer.model() is bar._theme_model
    # bare-name form still completes the whole line
    bar.setPlainText('nor')
    assert bar.completer.completionPrefix() == 'nor'


def test_theme_completion_has_no_trailing_space(theme_ctl, qapp):
    """Completing a theme replaces the name, no trailing separator."""
    bar = theme_ctl.command_bar
    bar.open('theme', lambda t: None)
    bar.setPlainText('theme:dra')
    bar.completer.setCompletionPrefix('dra')
    bar.handleCompletion('Dracula')
    assert bar.toPlainText() == 'theme:Dracula'


def test_theme_bar_completer_lists_registry_names(theme_ctl, qapp):
    names = theme_ctl.command_bar._theme_model.stringList()
    assert 'Dracula' in names
    assert 'nord' in names
# --- real keypress dispatch (keymap.py -> panel.Panel.keyPressEvent) -----
#
# Phase 2 added the handler methods and confirmed them by calling them
# directly. These confirm the actual keybindings are *reachable*: a real
# QKeyEvent, through Panel's real keymap dispatch, ends up calling them
# -- not just that the methods work in isolation.

@pytest.fixture
def theme_panel(theme_ctl, qapp):
    """A real Panel wired to theme_ctl, with the prefix cache populated
    from the real global_keymap (needed for the 't h' chord)."""
    p = DummyPanel(theme_ctl, 'test panel')
    p.set_keymap({})  # empty local keymap; prefixes still scan global_keymap
    return p


def _key_event(key, modifiers=Qt.KeyboardModifier.NoModifier):
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers)


def test_keypress_alt_less_cycles_theme_previous(theme_panel, theme_ctl, qapp):
    """Ctrl+< was the original choice, but 'C-<' collides as a literal
    string-prefix of the existing 'C-<enter>' binding (this codebase
    wraps named keys like <enter>/<tab> in angle brackets) -- the
    dispatcher can't tell "complete press of literal <" from "still
    typing toward <enter>", so it would wait out the full chord-timeout
    before firing instead of dispatching immediately. Alt avoids the
    collision entirely (verified: no other 'M-<...>' binding exists)."""
    from lazarus import themes as themes_mod
    names = themes_mod.ordered_names()
    theme_ctl.set_theme(names[1])
    theme_panel.keyPressEvent(
        _key_event(Qt.Key.Key_Less, Qt.KeyboardModifier.AltModifier))
    assert themes_mod.current_name() == names[0]


def test_keypress_alt_greater_cycles_theme_next(theme_panel, theme_ctl, qapp):
    from lazarus import themes as themes_mod
    names = themes_mod.ordered_names()
    theme_ctl.set_theme(names[0])
    theme_panel.keyPressEvent(
        _key_event(Qt.Key.Key_Greater, Qt.KeyboardModifier.AltModifier))
    assert themes_mod.current_name() == names[1]


def test_keypress_ctrl_less_does_not_cycle_theme(theme_panel, theme_ctl, qapp):
    """Guards the collision itself: Ctrl+< must NOT fire the theme
    binding immediately (it's swallowed into the 'C-<enter>' chord
    prefix instead) -- this is exactly why the binding uses Alt."""
    from lazarus import themes as themes_mod
    names = themes_mod.ordered_names()
    theme_ctl.set_theme(names[1])
    theme_panel.keyPressEvent(
        _key_event(Qt.Key.Key_Less, Qt.KeyboardModifier.ControlModifier))
    assert themes_mod.current_name() == names[1]  # unchanged -- still buffered
    assert theme_panel._prefix == 'C-<'


def test_keypress_t_h_chord_opens_theme_bar(theme_panel, theme_ctl, qapp):
    # 't' alone is a registered prefix (t t / t m / t h all start with
    # it) -- the first press should be swallowed into _prefix, not
    # dispatched, then 'h' completes the chord to 't h'.
    theme_panel.keyPressEvent(_key_event(Qt.Key.Key_T))
    assert theme_panel._prefix == 't'
    assert theme_ctl.command_bar.mode != 'theme'  # not yet

    theme_panel.keyPressEvent(_key_event(Qt.Key.Key_H))
    assert theme_ctl.command_bar.mode == 'theme'
    assert theme_panel._prefix == ''  # consumed


def test_theme_completion_closes_popup_and_enter_applies(theme_ctl, qapp):
    """Regression: after completing a theme the popup must be gone so
    Enter issues the command -- accept() bails while the popup is
    visible, which forced Esc+Enter before."""
    from lazarus import themes as themes_mod
    theme_ctl.main_window.tabs.show()  # popup visibility needs a shown parent
    theme_ctl.theme_bar()
    bar = theme_ctl.command_bar
    bar.setPlainText('theme:dra')           # popup appears
    assert bar.completer.popup().isVisible()
    bar.completer.setCompletionPrefix('dra')
    bar.handleCompletion('Dracula')         # fill in + close popup
    assert not bar.completer.popup().isVisible()
    assert bar.toPlainText() == 'theme:Dracula'
    bar.accept()                            # issues the command, no Esc needed
    assert themes_mod.current_name() == 'Dracula'


def test_theme_exact_name_typing_does_not_open_popup(theme_ctl, qapp):
    """Typing a full theme name manually doesn't pop the completer."""
    bar = theme_ctl.command_bar
    bar.open('theme', lambda t: None)
    bar.setPlainText('theme:Dracula')       # exact match -> no popup
    popup = bar.completer.popup()
    assert popup is None or not popup.isVisible()
