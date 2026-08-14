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

from PyQt6.QtCore import QObject, QSettings, QTimer, QThread, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from . import settings
from . import rules
from . import actions
from . import notmuch

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

    def __init__(self, parent: QObject=None) -> None:
        super().__init__(parent)
        self._procs: list[subprocess.Popen] = []
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

        # Dodo owns these for now; controller proxies via app.  Use
        # getattr so construction order does not matter if controller
        # is created before sync fields exist.
        self.panel_history: list["Panel"] = getattr(app, "panel_history", [])  # type: ignore[assignment]

        self.sync_thread: "SyncMailThread | None" = getattr(app, "sync_thread", None)  # type: ignore[assignment]
        self.sync_timer: QTimer | None = getattr(app, "sync_timer", None)

        self._wire_sync_timer()

    # -- panel orchestration (moved from Dodo) ------------------------------

    def raise_panel(self, p: "Panel") -> None:  # type: ignore[no-redef]
        self.tabs.setCurrentWidget(p)
        self.main_window.activateWindow()

    def show_help(self) -> None:
        """Show help window — lives on Dodo (AppController is plain QObject)."""
        # AppController doesn't own the HelpWindow; delegate to the app.
        try:
            self.app.show_help()  # type: ignore[attr-defined]
        except AttributeError:
            pass

    def message(self, title: str, body: str) -> None:
        QMessageBox.warning(self.main_window, title, body)

    def status_message(self, message: str, kind: str = 'info', duration: int = 3000) -> None:
        self.main_window.show_status(message, kind, duration)

    def navigate_list(self, direction: str) -> None:
        w = self.tabs.currentWidget()
        if w and hasattr(w, 'next_thread') and hasattr(w, 'previous_thread'):
            if direction == 'next':
                w.next_thread()  # type: ignore[attr-defined]
            elif direction == 'previous':
                w.previous_thread()  # type: ignore[attr-defined]

    def mark_and_advance(self) -> None:
        w = self.tabs.currentWidget()
        if w and hasattr(w, 'toggle_thread_tag') and hasattr(w, 'next_thread'):
            w.toggle_thread_tag('marked')  # type: ignore[attr-defined]
            w.next_thread()  # type: ignore[attr-defined]

    def delegate_to_list(self, method: str, **kwargs: object) -> None:
        from . import panel as panel_mod  # lazy to avoid cycle
        w = self.tabs.currentWidget()
        if w and hasattr(w, method):
            getattr(w, method)(**kwargs)

    def delegate_to_thread(self, method: str, **kwargs: object) -> None:
        tp = self.main_window.active_thread()
        if tp is not None and hasattr(tp, method):
            getattr(tp, method)(**kwargs)

    def toggle_tag_hotkey(self, key: str) -> None:
        tag = settings.tag_hotkeys.get(key)
        if not tag:
            return
        w = self.tabs.currentWidget()
        if w and hasattr(w, 'toggle_thread_tag'):
            w.toggle_thread_tag(tag)  # type: ignore[attr-defined]

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

        if not to_close:
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
        self.command_bar.setText(w.q)

    def tag_bar(self, mode: Literal['tag', 'tag marked'] = 'tag') -> None:
        from . import search as search_mod
        from . import thread as thread_mod
        from . import panel as panel_mod

        def callback(tag_expr: str) -> None:
            w = self.tabs.currentWidget()
            if w and isinstance(w, panel_mod.Panel):
                if isinstance(w, search_mod.SearchPanel):
                    w.tag_thread(tag_expr, mode)  # type: ignore[arg-type]
                elif isinstance(w, thread_mod.ThreadPanel):
                    w.tag_message(tag_expr)  # type: ignore[attr-defined]
                w.refresh()

        self.command_bar.open(mode, callback)

    def _wire_sync_timer(self) -> None:
        # Timer still lives on Dodo; controller will take ownership later.
        pass

    def sync_mail(self, quiet: bool = True) -> None:
        """Sync mail with IMAP server

        This method runs :func:`~lazarus.settings.sync_mail_command`, then 'notmuch new'

        :param quiet: If this is True, do not change the window title during sync.
                      Status bar messages are always shown."""

        if self.sync_thread is not None and self.sync_thread.isRunning():
            return

        t = SyncMailThread(parent=self.app)
        self.sync_thread = t
        self.app.sync_thread = t  # type: ignore[attr-defined]  # keep Dodo shim in sync

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
            self.sync_thread = None
            self.app.sync_thread = None  # type: ignore[attr-defined]
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
        if self.sync_timer is not None and self.sync_timer.isActive():
            self.sync_timer.stop()
        elif self.app.sync_timer is not None and self.app.sync_timer.isActive():  # type: ignore[attr-defined]
            self.app.sync_timer.stop()  # type: ignore[attr-defined]
        if self.sync_thread is not None and self.sync_thread.isRunning():
            self.sync_thread.stop()
        elif self.app.sync_thread is not None and self.app.sync_thread.isRunning():  # type: ignore[attr-defined]
            self.app.sync_thread.stop()  # type: ignore[attr-defined]
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
