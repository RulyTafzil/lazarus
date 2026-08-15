#     Lazarus - A fork of Dodo, a graphical, hackable email client based on notmuch
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
"""AppController — owns tab/panel orchestration, sync, and app-level commands.

Split from ``lazarus.app.Dodo`` (which subclasses ``QApplication``) so that
``Dodo`` stays a thin bootstrap (logging, config, signal plumbing, keymap
wiring) while all panel-registry and sync-orchestration logic lives here.

Motivation
----------
* ``Dodo(QApplication)`` was an 800-line god object: it owned the Qt app
  lifecycle, ``QSettings`` persistence, ``SyncMailThread`` lifecycle,
  command-bar delegation, and every ``open_*/close_panel/add_panel``
  method.  Panels imported ``app.Dodo`` for typing, closing the cycle
  ``app → search → app``.
* With a dedicated controller, panels depend on ``Controller`` (a plain
  ``QObject``), tests can instantiate a ``Controller`` with a stub
  ``QApplication``, and ``Dodo`` can defer panel imports.

This module is intentionally **not** imported by ``lazarus.app`` at load
time — it lazy-imports panel modules inside methods to break the cycle.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
from typing import TYPE_CHECKING, Optional, Literal

from PyQt6.QtCore import QObject, QSettings, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import settings
from . import rules
from . import actions
from . import notmuch
from .protocols import ThreadList, ThreadView, LIST_METHODS, THREAD_METHODS

if TYPE_CHECKING:
    from .mainwindow import MainWindow
    from .app import Dodo
    from .panel import Panel

logger = logging.getLogger(__name__)


class SyncMailThread(QThread):
    """A QThread used for syncing local Maildir and notmuch with IMAP.

    Runs ``mbsync -V <account>`` in parallel for every account found via
    ``mbsync -l``, then runs ``notmuch new`` once when all complete.
    """

    progress = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._procs: list[subprocess.Popen] = []
        self._proc: subprocess.Popen | None = None
        self._stopping = False
        self.sync_stderr: str = ''
        self.sync_rc: int = 0
        self.sync_summaries: list[str] = []
        self.notmuch_stderr: str = ''
        self.notmuch_rc: int = 0

    def run(self) -> None:
        """Run ``mbsync`` per account in parallel, then ``notmuch new``."""
        accounts = settings.smtp_accounts
        if not accounts:
            # No accounts configured; fall back to the shell command
            self.progress.emit('Syncing (all)...')
            self._run_single(settings.sync_mail_command)
        else:
            self._run_parallel(accounts)

        if self._stopping:
            return

        self.progress.emit('Indexing...')
        self._proc = subprocess.Popen(['notmuch', 'new'], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE,
                                      start_new_session=True,
                                      universal_newlines=True)
        assert self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if line.startswith('Processed'):
                self.progress.emit(line)
        self._proc.wait()
        self.notmuch_rc = self._proc.returncode
        if self._proc.stderr:
            self.notmuch_stderr = self._proc.stderr.read().strip()
        self._proc = None

    def _run_parallel(self, accounts: list[str]) -> None:
        """Spawn ``mbsync -V <acct>`` for every account in parallel."""
        import select

        procs: dict[int, tuple[subprocess.Popen, str]] = {}  # fd → (proc, account)
        for acct in accounts:
            self.progress.emit(f'Syncing: {acct}...')
            p = subprocess.Popen(['mbsync', '-V', acct],
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 start_new_session=True,
                                 universal_newlines=True)
            assert p.stdout is not None
            procs[p.stdout.fileno()] = (p, acct)
            self._procs.append(p)

        combined_stderr: list[str] = []
        summaries: list[str] = []
        done_accounts: set[str] = set()

        while procs:
            if self._stopping:
                break
            try:
                readable, _, _ = select.select(list(procs), [], [], 0.5)
            except (ValueError, OSError):
                break

            for fd in readable:
                proc, acct = procs[fd]
                assert proc.stdout is not None
                line = proc.stdout.readline()
                if not line:
                    # Process finished
                    proc.wait()
                    if proc.returncode != 0 and acct not in done_accounts:
                        self.sync_rc = proc.returncode
                    if proc.stderr:
                        stderr = proc.stderr.read().strip()
                        if stderr:
                            combined_stderr.append(f'{acct}: {stderr}')
                    done_accounts.add(acct)
                    del procs[fd]
                    continue

                line = line.strip()
                if not line:
                    continue
                if line.startswith('Opening far side box '):
                    box = line[21:].rstrip('...')
                    self.progress.emit(f'  {acct}: {box}')
                elif line.startswith('Channels:'):
                    summaries.append(f'{acct}: {line}')

        self.sync_stderr = '\n'.join(combined_stderr)
        self.sync_summaries = summaries

    def _run_single(self, cmd: str) -> None:
        """Run a single shell sync command (fallback for custom configs)."""
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             shell=True, start_new_session=True,
                             universal_newlines=True)
        self._procs = [p]
        assert p.stdout
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            if line.startswith('Channel '):
                self.progress.emit(f'Syncing: {line[8:]}...')
            elif line.startswith('Opening far side box '):
                box = line[21:].rstrip('...')
                self.progress.emit(f'  {box}')
            elif line.startswith('Channels:'):
                self.sync_summaries.append(line)
        p.wait()
        self.sync_rc = p.returncode
        if p.stderr:
            self.sync_stderr = p.stderr.read().strip()

    def _kill_procs(self) -> None:
        """Kill all running subprocesses and their process groups."""
        for proc in self._procs:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
        if hasattr(self, '_proc') and self._proc is not None:
            try:
                os.killpg(self._proc.pid, signal.SIGTERM)
            except OSError:
                pass

    def stop(self) -> None:
        """Terminate all running subprocesses and wait for the thread to finish."""
        self._stopping = True
        self._kill_procs()
        self.wait()


class AppController(QObject):
    """Panel registry + app-level commands, owned by ``Dodo``.

    Constructed inside ``Dodo.__init__`` after ``MainWindow`` exists;
    handed the ``QApplication`` so it can hook ``aboutToQuit`` and
    show dialogs parented to ``MainWindow``.

    All ``open_*`` methods previously on ``Dodo`` now live here; a thin
    delegation shim on ``Dodo`` keeps external callers (e.g. ``keymap``)
    working without changes.
    """

    def __init__(self, app: "Dodo", main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self.app = app
        self.main_window = main_window
        self.tabs = main_window.tabs
        self.command_bar = main_window.command_bar

        # Dodo owns the sync thread/timer; the controller reads them via
        # the app (getattr so construction order does not matter).
        self.panel_history: list["Panel"] = getattr(app, "panel_history", [])  # type: ignore[assignment]

    # -- panel orchestration (moved from Dodo) ------------------------------

    def raise_panel(self, p: "Panel") -> None:  # type: ignore[no-redef]
        self.tabs.setCurrentWidget(p)
        self.main_window.activateWindow()

    def show_help(self) -> None:
        """Show the help window (owned by Dodo)."""
        self.app.show_help()

    def message(self, title: str, body: str) -> None:
        QMessageBox.warning(self.main_window, title, body)

    def status_message(self, message: str, kind: str = 'info', duration: int = 3000) -> None:
        self.main_window.show_status(message, kind, duration)

    def navigate_list(self, direction: str) -> None:
        w = self.tabs.currentWidget()
        if isinstance(w, ThreadList):
            if direction == 'next':
                w.next_thread()
            elif direction == 'previous':
                w.previous_thread()
        else:
            logger.warning('navigate_list: current tab is not a thread list')

    def mark_and_advance(self) -> None:
        w = self.tabs.currentWidget()
        if isinstance(w, ThreadList):
            w.toggle_thread_tag('marked')
            w.next_thread()

    def delegate_to_list(self, method: str, **kwargs: object) -> None:
        """Call *method* on the current list tab, if it is a thread list.

        Fail-fast: unknown method names (typos) are logged instead of
        silently doing nothing.
        """
        w = self.tabs.currentWidget()
        if not isinstance(w, ThreadList):
            return
        if method not in LIST_METHODS:
            logger.warning('delegate_to_list: unknown method %r', method)
            return
        getattr(w, method)(**kwargs)

    def delegate_to_thread(self, method: str, **kwargs: object) -> None:
        """Call *method* on the active thread preview, if any."""
        tp = self.main_window.active_thread()
        if not isinstance(tp, ThreadView):
            return
        if method not in THREAD_METHODS:
            logger.warning('delegate_to_thread: unknown method %r', method)
            return
        getattr(tp, method)(**kwargs)

    def reply(self, to_all: bool = True) -> None:
        """Reply from the focused context.

        With the thread preview focused, replies to its current message
        (J/K selection); otherwise replies to the search list's selected
        thread (its most recent email), without opening it.
        """
        tp = self.main_window.active_thread()
        fw = QApplication.focusWidget()
        if (tp is not None and isinstance(tp, ThreadView)
                and fw is not None
                and (fw is tp or tp.isAncestorOf(fw))):
            tp.reply(to_all=to_all)
            return
        w = self.tabs.currentWidget()
        if isinstance(w, ThreadList):
            w.reply(to_all=to_all)

    def forward(self) -> None:
        """Forward from the focused context (same rule as :meth:`reply`)."""
        tp = self.main_window.active_thread()
        fw = QApplication.focusWidget()
        if (tp is not None and isinstance(tp, ThreadView)
                and fw is not None
                and (fw is tp or tp.isAncestorOf(fw))):
            tp.forward()
            return
        w = self.tabs.currentWidget()
        if isinstance(w, ThreadList):
            w.forward()

    def toggle_tag_hotkey(self, key: str) -> None:
        tag = settings.tag_hotkeys.get(key)
        if not tag:
            return
        w = self.tabs.currentWidget()
        if isinstance(w, ThreadList):
            w.toggle_thread_tag(tag)

    def add_panel(self, p: "Panel", focus: bool = True) -> None:
        self.tabs.addTab(p, p.title())
        p.has_refreshed.connect(self.refresh_tab_titles)  # type: ignore[attr-defined]
        if focus:
            self.tabs.setCurrentWidget(p)
            p.setFocus()

    def next_panel(self) -> None:
        i = self.tabs.currentIndex() + 1
        if i < self.tabs.count():
            self.tabs.setCurrentIndex(i)

    def previous_panel(self) -> None:
        i = self.tabs.currentIndex() - 1
        if i >= 0:
            self.tabs.setCurrentIndex(i)

    def close_panel(self, to_close: int | "Panel" | None = None) -> None:
        from . import panel as panel_mod
        if isinstance(to_close, panel_mod.Panel):
            if to_close is self.main_window.active_thread():
                self.main_window.clear_thread()
                self.main_window.focus_list()
                return

        if to_close is None:
            index = self.tabs.currentIndex()
        elif isinstance(to_close, int):
            index = to_close
        else:
            index = self.tabs.indexOf(to_close)

        w = self.tabs.widget(index)
        if w and isinstance(w, panel_mod.Panel) and not w.keep_open:
            if w.before_close():
                if w in self.panel_history:
                    self.panel_history.remove(w)
                if len(self.panel_history) > 0:
                    w0 = self.panel_history.pop()
                    self.tabs.setCurrentWidget(w0)
                self.tabs.removeTab(index)

    def open_search(self, query: str, keep_open: bool = False) -> None:
        if not query:
            return
        from . import search
        for i in range(self.num_panels()):
            w = self.tabs.widget(i)
            if isinstance(w, search.SearchPanel) and w.q == query:
                self.tabs.setCurrentIndex(i)
                return
        p = search.SearchPanel(self, query, keep_open=keep_open)  # type: ignore[arg-type]
        self.add_panel(p)

    def open_thread(self, thread_id: str, query: str) -> None:
        from . import thread as thread_mod
        active = self.main_window.active_thread()
        if isinstance(active, thread_mod.ThreadPanel) and active.thread_id == thread_id:  # type: ignore[attr-defined]
            # Already showing this thread — don't tear it down and
            # rebuild it, which would discard the reader's current
            # position.  Just refocus.
            self.main_window.thread_container.setCurrentWidget(active)
            active.setFocus()
            return
        p = thread_mod.ThreadPanel(self, thread_id, query)  # type: ignore[arg-type]
        self.main_window.show_thread(p)

    def open_compose(self, mode: str = '', msg: Optional[dict] = None) -> None:
        from . import compose
        p = compose.ComposePanel(self, mode, msg)  # type: ignore[arg-type]
        self.add_panel(p)

    def open_tags(self, keep_open: bool = False) -> None:
        from . import tag as tag_mod
        for i in range(self.num_panels()):
            w = self.tabs.widget(i)
            if isinstance(w, tag_mod.TagPanel):
                w.keep_open = keep_open  # type: ignore[attr-defined]
                self.tabs.setCurrentIndex(i)
                return
        p = tag_mod.TagPanel(self, keep_open)  # type: ignore[arg-type]
        self.add_panel(p)

    def search_bar(self) -> None:
        self.command_bar.open('search', callback=self.open_search)

    def edit_search_query(self) -> None:
        from . import search
        w = self.tabs.currentWidget()
        if not isinstance(w, search.SearchPanel):
            return
        self.command_bar.open('search', callback=w.set_query)  # type: ignore[arg-type]
        self.command_bar.setPlainText(w.q)

    def tag_bar(self, mode: Literal['tag', 'tag marked'] = 'tag') -> None:
        from . import search as search_mod
        from . import thread as thread_mod
        from . import panel as panel_mod

        def callback(tag_expr: str) -> None:
            # Nothing to do if the expression has no real tag tokens
            # (e.g. the prefilled '+', or an empty/'-'-only input).
            if not [t for t in tag_expr.split() if t.strip('+-')]:
                return
            w = self.tabs.currentWidget()
            if w and isinstance(w, panel_mod.Panel):
                if isinstance(w, search_mod.SearchPanel):
                    w.tag_thread(tag_expr, mode)  # type: ignore[arg-type]
                elif isinstance(w, thread_mod.ThreadPanel):
                    w.tag_message(tag_expr)  # type: ignore[attr-defined]
                w.refresh()

        self.command_bar.open(mode, callback)
        # Default action is to add a tag: prefill '+' so the user just
        # types the tag name; delete it and type '-' to remove instead.
        self.command_bar.setPlainText('+')
        self.command_bar._cursor_to_end()

    def tag_message_bar(self) -> None:
        """Tag the current message in the thread preview (C-t)."""
        tp = self.main_window.active_thread()
        if not isinstance(tp, ThreadView):
            return

        def callback(tag_expr: str) -> None:
            if not [t for t in tag_expr.split() if t.strip('+-')]:
                return
            tp.tag_message(tag_expr)

        self.command_bar.open('tag message', callback)
        self.command_bar.setPlainText('+')
        self.command_bar._cursor_to_end()

    def sync_mail(self, quiet: bool = True) -> None:
        """Sync mail with IMAP server

        This method runs :func:`~lazarus.settings.sync_mail_command`, then 'notmuch new'

        :param quiet: If this is True, do not change the window title during sync.
                      Status bar messages are always shown."""

        t = getattr(self.app, 'sync_thread', None)
        if t is not None and t.isRunning():
            return

        t = SyncMailThread(parent=self.app)
        self.app.sync_thread = t

        def done() -> None:
            if t.notmuch_rc == 0 and settings.filter_rules:
                try:
                    rules.apply_rules(settings.filter_rules, settings.filter_scope_query)
                except Exception as e:
                    logger.warning('Error applying filter rules: %s', e)
            self.refresh_panels()
            self.refresh_tab_titles()
            # Parse mbsync summary for status bar
            if t.sync_rc != 0:
                if t.sync_stderr:
                    logger.error('mbsync failed (exit %d): %s', t.sync_rc, t.sync_stderr)
                msg = f'Sync error (exit {t.sync_rc})'
                if t.sync_stderr:
                    msg += f': {t.sync_stderr[:200]}'
                self.status_message(msg, 'error', duration=8000)
            elif t.notmuch_rc != 0:
                if t.notmuch_stderr:
                    logger.error('notmuch failed (exit %d): %s', t.notmuch_rc, t.notmuch_stderr)
                msg = f'notmuch error (exit {t.notmuch_rc})'
                if t.notmuch_stderr:
                    msg += f': {t.notmuch_stderr[:200]}'
                self.status_message(msg, 'error', duration=8000)
            else:
                # Aggregate Far: stats across all accounts
                import re
                new = flagged = expunged = deleted = 0
                for summary in t.sync_summaries:
                    m = re.search(r'Far:\s*\+(\d+)\s*\*(\d+)\s*#(\d+)\s*-(\d+)', summary)
                    if m:
                        new += int(m.group(1))
                        flagged += int(m.group(2))
                        expunged += int(m.group(3))
                        deleted += int(m.group(4))

                bits = []
                if new != 0: bits.append(f'+{new} new')
                if flagged != 0: bits.append(f'*{flagged} flagged')
                if expunged != 0: bits.append(f'{expunged} cleaned')
                if deleted != 0: bits.append(f'{deleted} deleted')
                if bits:
                    self.status_message('Sync: ' + ', '.join(bits), 'info')
                else:
                    self.status_message('Sync: up to date', 'info')
            if not quiet:
                title = self.main_window.windowTitle()
                self.main_window.setWindowTitle(title.replace(' [syncing]', ''))
                self.main_window.update()
            self.app.sync_thread = None
            t.deleteLater()

        self.status_message('Syncing...', 'info')
        t.progress.connect(lambda msg: self.status_message(msg, 'info', duration=0))
        if not quiet:
            title = self.main_window.windowTitle()
            self.main_window.setWindowTitle(title + ' [syncing]')
            self.main_window.update()

        t.finished.connect(done)
        t.start()

    def apply_filter_rules(self) -> None:
        if not settings.filter_rules:
            self.status_message('No filter_rules configured', 'info')
            return
        try:
            n = rules.apply_rules(settings.filter_rules, settings.filter_scope_query)
        except Exception as e:
            logger.warning('Error applying filter rules: %s', e)
            self.status_message(f'Error applying filter rules: {e}', 'error')
            return
        self.refresh_panels()
        self.status_message(f'Applied filter rules ({n} matched)', 'info')

    def expunge_trash(self) -> None:
        count = notmuch.count('tag:trash', output='files')
        if count == 0:
            self.status_message('Trash is empty', 'info')
            return
        reply = QMessageBox.warning(
            self.main_window, 'Empty trash',
            f'Permanently delete {count} message{"s" if count != 1 else ""} from trash?\n\nThis cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        tagged = actions.expunge_trash()
        self.refresh_panels()
        if tagged:
            self.status_message(f'{tagged} message{"s" if tagged != 1 else ""} will be expunged on next sync', 'info')
        else:
            self.status_message('Nothing to expunge', 'info')

    def num_panels(self) -> int:
        return self.tabs.count()

    def refresh_tab_titles(self) -> None:
        from . import panel as panel_mod
        for i in range(self.num_panels()):
            w = self.tabs.widget(i)
            if isinstance(w, panel_mod.Panel):
                self.tabs.setTabText(i, w.title())

    def refresh_panels(self) -> None:
        from . import panel as panel_mod
        for i in range(self.num_panels()):
            w = self.tabs.widget(i)
            if isinstance(w, panel_mod.Panel):
                w.dirty = True
        w = self.tabs.currentWidget()
        if w and isinstance(w, panel_mod.Panel):
            w.refresh()
        tp = self.main_window.active_thread()
        if tp is not None:
            tp.dirty = True  # type: ignore[attr-defined]
            tp.refresh()

    def set_theme(self, name: str) -> None:
        """Switch the active theme live.

        `themes.set_theme` handles applying the palette/stylesheet and
        persisting the choice; `style.py`'s caches self-invalidate (keyed
        on the new theme dict's id / actual color values), and open
        panels already read `settings.theme` live at render time -- so
        what's left here is nudging the things that *don't*: the tab
        widget's cached mesh, and forcing a repaint of open panels.
        """
        from . import themes
        from . import mainwindow
        if not name:
            return
        try:
            themes.set_theme(name)
        except themes.ThemeError as e:
            self.status_message(str(e), kind='error')
            return
        if isinstance(self.tabs, mainwindow.WatermarkTabWidget):
            self.tabs.invalidate_mesh()
        self.refresh_panels()
        self.status_message(f"Theme: {name}", kind='info')

    def cycle_theme(self, direction: int) -> None:
        """Switch to the next/previous theme in `themes.ordered_names()`.

        :param direction: +1 for next, -1 for previous.
        """
        from . import themes
        names = themes.ordered_names()
        if not names:
            return
        current = themes.current_name()
        if current in names:
            idx = (names.index(current) + direction) % len(names)
        else:
            # Unknown current theme (e.g. a raw dict set directly in
            # config.py with no matching REGISTRY entry) -- start from
            # one end rather than guessing.
            idx = 0 if direction > 0 else -1
        self.set_theme(names[idx])

    def theme_bar(self) -> None:
        """Open the command bar in 'theme' mode: type a name, Enter to
        apply. Autocompletes against `themes.REGISTRY` (commandbar.py)."""
        self.command_bar.open('theme', lambda text: self.set_theme(text.strip()))

    def update_single_thread(self, thread_id: str, msg_id: str | None = None) -> None:
        from . import panel as panel_mod
        from . import thread as thread_mod
        current = self.tabs.currentWidget()
        for i in range(self.num_panels()):
            w = self.tabs.widget(i)
            if isinstance(w, panel_mod.Panel):
                w.update_thread(thread_id, msg_id=msg_id)
                if w == current and w.dirty:
                    w.refresh()
        tp = self.main_window.active_thread()
        if tp is not None and isinstance(tp, thread_mod.ThreadPanel) and tp.thread_id == thread_id:  # type: ignore[attr-defined]
            tp.update_thread(thread_id, msg_id=msg_id)  # type: ignore[attr-defined]
            # update_thread sets dirty=True only when it could *not* do a
            # cheap single-message refresh (e.g. msg_id not in model).
            # Only force a full refresh in that case — otherwise the
            # full model.reset tears down the tree and jumps the view.
            if tp.dirty:
                tp.refresh()

    def _cleanup_sync(self) -> None:
        timer = getattr(self.app, 'sync_timer', None)
        if timer is not None and timer.isActive():
            timer.stop()
        thread = getattr(self.app, 'sync_thread', None)
        if thread is not None and thread.isRunning():
            thread.stop()
        # Join bulk-move worker so Quit never races a pending batch_done emit.
        try:
            from . import actions as actions_mod
            actions_mod.shutdown_worker()
        except Exception:
            pass

    def prompt_quit(self) -> None:
        self._save_open_searches()
        self.main_window.close()

    def _save_open_searches(self) -> None:
        from . import search
        conf = QSettings('lazarus', 'lazarus')
        queries: list[str] = []
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, search.SearchPanel) and not w.keep_open:
                queries.append(w.q)
        conf.setValue('open_searches', queries)

    def _restore_open_searches(self) -> None:
        conf = QSettings('lazarus', 'lazarus')
        queries = conf.value('open_searches')
        if queries:
            for q in queries:
                self.open_search(q)
