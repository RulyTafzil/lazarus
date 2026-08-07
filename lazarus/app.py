#     Lazarus - A fork of Dodo, a graphical, hackable email client based on notmuch
#     Copyright (C) 2021 - Aleks Kissinger
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
from __future__ import annotations
import os

from PyQt6.QtCore import *
from PyQt6.QtWidgets import *
from PyQt6.QtWebEngineCore import QWebEngineUrlScheme
import sys
import signal
import fcntl
import subprocess
from typing import Optional, Literal
import logging

from . import search
from . import thread
from . import compose
from . import tag

from . import settings
from . import themes
from . import util
from . import keymap
from . import commandbar
from . import helpwindow
from . import panel
from . import mainwindow
from . import rules
from . import actions
from . import address_completer
from . import notmuch
from .webengine import LOCAL_PROTOCOLS

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


class Dodo(QApplication):
    """The main Lazarus application

    There is always one instance of this class, and it contains methods for all of the global (i.e.
    not view-specific) commands. This includes running global opening/closing panels, opening the help
    window, and synchronizing mail with the IMAP server.
    """

    def __init__(self) -> None:
        super().__init__(sys.argv)

        # Minimal stderr logger so early errors are captured.
        # Full configuration (level + file) happens after config.py loads.
        logging.basicConfig(
            level=logging.WARNING,
            format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            handlers=[logging.StreamHandler(sys.stderr)],
        )

        self.setApplicationName('Lazarus')
        self.setDesktopFileName("lazarus")

        # find a load config.py
        self.config_file = QStandardPaths.locate(QStandardPaths.StandardLocation.ConfigLocation, 'lazarus/config.py')
        if self.config_file:
            try:
                exec(open(self.config_file).read())
            except Exception as e:
                print(f'Error loading config file {self.config_file}: {e}', file=sys.stderr)
                sys.exit(1)
        else:
            config_locs = QStandardPaths.standardLocations(QStandardPaths.StandardLocation.ConfigLocation)
            print('No config.py found in:\n' + '\n'.join([f'  {d}/lazarus' for d in config_locs]))
            sys.exit(1)

        # Reconfigure logging now that user settings are available.
        self._setup_logging()

        # Apply dark-mode Chromium flag if configured (must be set
        # before any QWebEngineView loads content)
        if settings.force_dark_mode:
            flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
            if '--force-dark-mode' not in flags:
                flags = f'{flags} --force-dark-mode'.strip()
                os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = flags

        # construct help window
        self.help_window = helpwindow.HelpWindow()

        # apply theme
        themes.apply_theme(settings.theme)

        # register custom URL schemes used by embedded HTML viewer
        for proto in LOCAL_PROTOCOLS:
            scheme = QWebEngineUrlScheme(proto.encode('utf-8'))
            scheme.setSyntax(QWebEngineUrlScheme.Syntax.Path)
            QWebEngineUrlScheme.registerScheme(scheme)

        # set up GUI
        self.panel_history = []
        self.main_window = mainwindow.MainWindow(self)
        self.tabs = self.main_window.tabs
        self.command_bar = self.main_window.command_bar
        self.lastWindowClosed.connect(self.quit)

        # Controller owns panel registry + commands; Dodo keeps shims
        # so keymap (which is typed against Dodo) keeps working.
        from .controller import AppController
        self.controller = AppController(self, self.main_window)

        # set timer to sync email periodically
        self.sync_thread: SyncMailThread | None = None
        self.sync_timer: QTimer | None = None
        if settings.sync_mail_interval != -1:
            self.sync_mail()
            self.sync_timer = QTimer(self)
            self.sync_timer.timeout.connect(self.sync_mail)
            self.sync_timer.start(settings.sync_mail_interval * 1000)

        self.aboutToQuit.connect(self._cleanup_sync)

        # Refresh panels after background file moves (filter, trash,
        # archive) complete and notmuch new finishes re-indexing.
        actions._get_worker().batch_done.connect(self.refresh_panels)
        actions._get_worker().batch_done.connect(self.controller.refresh_panels)  # type: ignore[attr-defined]

        # Preload the address book in the background so autocomplete
        # is ready by the time the user opens the compose panel.
        address_completer.preload_addresses()

        # Handle Ctrl-C: use a pipe + QSocketNotifier so the Qt event loop
        # wakes up immediately when a Unix signal arrives.
        self._signal_read_fd, self._signal_write_fd = os.pipe()
        fcntl.fcntl(self._signal_read_fd, fcntl.F_SETFL, os.O_NONBLOCK)
        fcntl.fcntl(self._signal_write_fd, fcntl.F_SETFL, os.O_NONBLOCK)
        signal.set_wakeup_fd(self._signal_write_fd)

        self._signal_notifier = QSocketNotifier(
            self._signal_read_fd, QSocketNotifier.Type.Read, self)
        self._signal_notifier.activated.connect(self._handle_signal_wakeup)

        signal.signal(signal.SIGINT, lambda *_: None)

        # open init_queries and make un-closeable
        for query in settings.init_queries:
            self.open_search(query, keep_open=True)

        # Restore search panels from previous session
        self._restore_open_searches()

    @staticmethod
    def _setup_logging() -> None:
        """Reconfigure logging from settings.

        Called after config.py loads so user-configured ``log_level``
        and ``log_file`` take effect.  Uses the root logger (already
        has a stderr handler from early init), just adjusts level and
        optionally adds a file handler.
        """
        level_name = settings.log_level.upper()
        if '--verbose' in sys.argv or '-v' in sys.argv:
            level_name = 'INFO'
        level = getattr(logging, level_name, logging.WARNING)

        root = logging.getLogger()
        root.setLevel(level)

        if settings.log_file:
            log_path = os.path.expanduser(settings.log_file)
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            fh = logging.FileHandler(log_path)
            fh.setFormatter(logging.Formatter(
                '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'))
            root.addHandler(fh)

        logger = logging.getLogger(__name__)
        logger.info('Logging initialised (level=%s, file=%s)',
                     level_name, settings.log_file or 'stderr only')

    def _handle_signal_wakeup(self) -> None:
        """Called when a Unix signal wakes up the Qt event loop via the pipe"""
        self._signal_notifier.setEnabled(False)
        try:
            os.read(self._signal_read_fd, 4096)
        except OSError:
            pass
        self._cleanup_sync()
        self.quit()

    def show_help(self) -> None:
        """Show help window"""

        self.help_window.show()

    def raise_panel(self, p: panel.Panel) -> None:
        self.tabs.setCurrentWidget(p)
        self.main_window.activateWindow()
        # self.main_window.setWindowState(self.main_window.windowState() ^ Qt.WindowActive)

    def message(self, title, body) -> None:
        QMessageBox.warning(self.main_window, title, body)

    def status_message(self, message: str, kind: str = 'info', duration: int = 3000) -> None:
        """Show a transient status bar message."""
        self.main_window.show_status(message, kind, duration)

    def navigate_list(self, direction: str) -> None:
        """Navigate the current list panel's thread selection.

        Always targets the list side, even when the thread preview has focus.
        """
        w = self.tabs.currentWidget()
        if w and hasattr(w, 'next_thread') and hasattr(w, 'previous_thread'):
            if direction == 'next':
                w.next_thread()
            elif direction == 'previous':
                w.previous_thread()

    def mark_and_advance(self) -> None:
        """Toggle marked on the current thread and advance, list-side."""
        w = self.tabs.currentWidget()
        if w and hasattr(w, 'toggle_thread_tag') and hasattr(w, 'next_thread'):
            w.toggle_thread_tag('marked')
            w.next_thread()

    def delegate_to_list(self, method: str, **kwargs: object) -> None:
        """Call *method* on the current list panel with *kwargs*.

        Always targets the list tab, even when the thread preview
        has focus.  Silently no-ops if the panel doesn't have the method.
        """
        w = self.tabs.currentWidget()
        if w and hasattr(w, method):
            getattr(w, method)(**kwargs)

    def delegate_to_thread(self, method: str, **kwargs: object) -> None:
        """Call *method* on the active thread preview with *kwargs*.

        Silently no-ops if no thread is showing or the method is missing.
        """
        tp = self.main_window.active_thread()
        if tp is not None and hasattr(tp, method):
            getattr(tp, method)(**kwargs)

    def toggle_tag_hotkey(self, key: str) -> None:
        """Toggle the tag configured for hotkey *key* on the current
        list panel's selected thread.  Used by global 1-9 hotkeys."""
        tag = settings.tag_hotkeys.get(key)
        if not tag:
            return
        w = self.tabs.currentWidget()
        if w and hasattr(w, 'toggle_thread_tag'):
            w.toggle_thread_tag(tag)

    def add_panel(self, p: panel.Panel, focus: bool=True) -> None:
        """Add a panel to the tab view

        This method is used by the :func:`search`, :func:`thread`, and :func:`compose`
        methods to open new panels. In general, this method shouldn't be called directly
        from key mappings."""

        self.tabs.addTab(p, p.title())
        p.has_refreshed.connect(self.refresh_tab_titles)

        if focus:
            self.tabs.setCurrentWidget(p)
            p.setFocus()

    def next_panel(self) -> None:
        """Go to the next panel"""

        i = self.tabs.currentIndex() + 1
        if i < self.tabs.count():
            self.tabs.setCurrentIndex(i)

    def previous_panel(self) -> None:
        """Go to the previous panel"""

        i = self.tabs.currentIndex() - 1
        if i >= 0:
            self.tabs.setCurrentIndex(i)

    def close_panel(self, to_close: int|panel.Panel|None=None) -> None:
        """Close the panel at `index` (if provided) or the current panel

        Only closes panels in the tab bar, never the thread preview pane.
        If the panel to close is the active thread preview, it is
        cleared (replaced with placeholder) instead.
        """

        if isinstance(to_close, panel.Panel):
            # Check if it's the thread preview pane
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
        if w and isinstance(w, panel.Panel) and not w.keep_open:
            if w.before_close():
                # remove this panel from the history
                if w in self.panel_history:
                    self.panel_history.remove(w)
                # focus the last focused panel
                if len(self.panel_history) > 0:
                    w0 = self.panel_history.pop()
                    self.tabs.setCurrentWidget(w0)
                # remove the panel itself
                self.tabs.removeTab(index)

    def open_search(self, query: str, keep_open: bool=False) -> None:
        """Open a search panel with the given query

        If a panel with this query is already open, switch to it rather than
        opening another copy."""
        if not query:
            return

        for i in range(self.num_panels()):
            w = self.tabs.widget(i)
            if isinstance(w, search.SearchPanel) and w.q == query:
                self.tabs.setCurrentIndex(i)
                return

        p = search.SearchPanel(self, query, keep_open=keep_open)
        self.add_panel(p)

    def open_thread(self, thread_id: str, query: str) -> None:
        """Open a thread in the persistent preview pane.

        Replaces any previously shown thread.  Does NOT create a tab.
        """
        p = thread.ThreadPanel(self, thread_id, query)
        self.main_window.show_thread(p)

    def open_compose(self, mode: str='', msg: Optional[dict]=None) -> None:
        """Open a compose panel

        If reply_to is provided, set populate the 'To' and 'In-Reply-To' headers
        appropriately, and quote the text in this message.

        :param msg: A JSON message referenced in a reply or forward
        :param mode: Composition mode. Possible values are '', 'reply', 'replyall',
                     and 'forward'
        """

        p = compose.ComposePanel(self, mode, msg)
        self.add_panel(p)

    def open_tags(self, keep_open: bool=False) -> None:
        """Open tag panel"""

        for i in range(self.num_panels()):
            w = self.tabs.widget(i)
            if isinstance(w, tag.TagPanel):
                w.keep_open = keep_open
                self.tabs.setCurrentIndex(i)
                return

        p = tag.TagPanel(self, keep_open)
        self.add_panel(p)

    def search_bar(self) -> None:
        """Open command bar for searching"""
        self.command_bar.open('search', callback=self.open_search)

    def edit_search_query(self) -> None:
        """Open command bar pre-filled with the current search tab's query.

        Editing and pressing Enter replaces the tab's query in-place rather
        than opening a new tab.  Bound to ``C-/``.
        """
        w = self.tabs.currentWidget()
        if not isinstance(w, search.SearchPanel):
            return
        self.command_bar.open('search', callback=w.set_query)
        self.command_bar.setText(w.q)

    def tag_bar(self, mode: Literal['tag', 'tag marked']='tag') -> None:
        """Open command bar for tagging"""
        def callback(tag_expr: str) -> None:
            w = self.tabs.currentWidget()
            if w and isinstance(w, panel.Panel):
                if isinstance(w, search.SearchPanel): w.tag_thread(tag_expr, mode)
                elif isinstance(w, thread.ThreadPanel): w.tag_message(tag_expr)

                w.refresh()
        self.command_bar.open(mode, callback)

    def sync_mail(self, quiet: bool=True) -> None:
        """Sync mail with IMAP server

        This method runs :func:`~lazarus.settings.sync_mail_command`, then 'notmuch new'

        :param quiet: If this is True, do not change the window title during sync.
                      Status bar messages are always shown."""

        if self.sync_thread is not None and self.sync_thread.isRunning():
            return

        t = SyncMailThread(parent=self)
        self.sync_thread = t

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
        """Manually (re-)apply :func:`~lazarus.settings.filter_rules`

        Runs the same rules :func:`sync_mail` applies automatically after
        every sync, against the same :func:`~lazarus.settings.filter_scope_query`
        scope. Useful for testing a rule you just added without waiting for
        (or forcing) a full sync.
        """
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
        """Permanently expunge all messages tagged ``trash``.

        Runs :func:`lazarus.actions.expunge_trash` to add the Maildir
        ``T`` flag to every file in a Trash folder.  Shows a
        confirmation dialog with a message count first — this action
        is irreversible.

        Bound to the ``d d`` keychord.
        """
        # Count first, confirm, then expunge
        count = notmuch.count('tag:trash', output='files')

        if count == 0:
            self.status_message('Trash is empty', 'info')
            return

        reply = QMessageBox.warning(
            self.main_window,
            'Empty trash',
            f'Permanently delete {count} message{"s" if count != 1 else ""} '
            f'from trash?\n\nThis cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        tagged = actions.expunge_trash()
        self.refresh_panels()
        if tagged:
            self.status_message(
                f'{tagged} message{"s" if tagged != 1 else ""} '
                f'will be expunged on next sync', 'info')
        else:
            self.status_message('Nothing to expunge', 'info')

    def num_panels(self) -> int:
        """Returns the number of panels (i.e. tabs) currently open"""

        return self.tabs.count()

    def refresh_tab_titles(self) -> None:
        for i in range(self.num_panels()):
            w = self.tabs.widget(i)
            if isinstance(w, panel.Panel):
                self.tabs.setTabText(i, w.title())

    def refresh_panels(self) -> None:
        """Refresh current panel and mark the others as out of date

        This method gets called whenever tags have been changed or a new message has
        been sent. The refresh will happen the next time a panel is switched to."""

        for i in range(self.num_panels()):
            w = self.tabs.widget(i)
            if isinstance(w, panel.Panel):
                w.dirty = True

        w = self.tabs.currentWidget()
        if w and isinstance(w, panel.Panel):
            w.refresh()

        # Also mark the thread preview dirty
        tp = self.main_window.active_thread()
        if tp is not None:
            tp.dirty = True
            tp.refresh()

    def update_single_thread(self, thread_id: str, msg_id: str|None=None):
        current = self.tabs.currentWidget()
        for i in range(self.num_panels()):
            w = self.tabs.widget(i)
            if isinstance(w, panel.Panel):
                w.update_thread(thread_id, msg_id=msg_id)
                if w == current and w.dirty:
                    w.refresh()

        # Also update the thread preview if it shows this thread
        tp = self.main_window.active_thread()
        if tp is not None:
            if isinstance(tp, thread.ThreadPanel) and tp.thread_id == thread_id:
                tp.update_thread(thread_id, msg_id=msg_id)
                tp.refresh()

    def _cleanup_sync(self) -> None:
        """Stop the sync timer and terminate any running sync thread"""
        if self.sync_timer is not None:
            self.sync_timer.stop()
        if self.sync_thread is not None and self.sync_thread.isRunning():
            self.sync_thread.stop()

    def prompt_quit(self) -> None:
        """A 'soft' quit function, which gives each open tab the opportunity to prompt
        the user and possible cancel closing."""
        self._save_open_searches()
        self.main_window.close()

    def _save_open_searches(self) -> None:
        """Save non-keep-open search queries to QSettings."""
        conf = QSettings('lazarus', 'lazarus')
        queries = []
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, search.SearchPanel) and not w.keep_open:
                queries.append(w.q)
        conf.setValue('open_searches', queries)

    def _restore_open_searches(self) -> None:
        """Restore search panels from the previous session."""
        conf = QSettings('lazarus', 'lazarus')
        queries = conf.value('open_searches')
        if queries:
            for q in queries:
                self.open_search(q)


_DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Version=1.0
Name=Lazarus
Comment=Lazarus email client
Exec=lazarus
Icon=lazarus
Terminal=false
"""


def install_desktop() -> None:
    """Install .desktop file and hicolor icons into ~/.local/share.

    Desktop environments (and ``QIcon.fromTheme``) look for icons under
    ``~/.local/share/icons/hicolor/`` by default.  This copies the bundled
    PNGs there so the app appears with its proper icon in launchers, docks,
    and alt-tab switchers, and writes the desktop entry so Lazarus shows up
    in application menus.
    """
    import shutil

    xdg_data = os.path.join(os.path.expanduser('~'), '.local', 'share')
    icons_dst = os.path.join(xdg_data, 'icons', 'hicolor')
    apps_dst = os.path.join(xdg_data, 'applications')

    # -- icons (bundled as package_data) ---------------------------------
    icons_src = os.path.join(os.path.dirname(__file__), 'icons', 'hicolor')
    if os.path.isdir(icons_src):
        for root, dirs, files in os.walk(icons_src):
            for f in files:
                src = os.path.join(root, f)
                rel = os.path.relpath(src, icons_src)
                dst = os.path.join(icons_dst, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                print(f'  {rel}')
        print('Icons installed to', icons_dst)
    else:
        print('No bundled icons found at', icons_src, file=sys.stderr)
        sys.exit(1)

    # -- desktop entry (embedded) ---------------------------------------
    os.makedirs(apps_dst, exist_ok=True)
    desktop_path = os.path.join(apps_dst, 'lazarus.desktop')
    with open(desktop_path, 'w') as f:
        f.write(_DESKTOP_ENTRY)
    print('Desktop entry installed to', desktop_path)

    # Update the icon cache so Qt/GNOME/KDE pick up the new icons
    # immediately without needing a logout.
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', apps_dst],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print('\nDone. You may need to log out and back in for some launchers to refresh.')


def main() -> None:
    """Main entry point for Lazarus"""

    if '--install-desktop' in sys.argv:
        install_desktop()
        return

    lazarus = Dodo()
    lazarus.exec()
