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

from .controller import SyncMailThread  # canonical impl lives in controller.py


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

        # find & validate config.py (via lazarus.config)
        from .config import load_config, ConfigError
        try:
            self.config_file, _warnings = load_config()
        except ConfigError as e:
            print(f"\n{e}\n", file=sys.stderr)
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
        # Single wiring via controller; Dodo.refresh_panels delegates there.
        actions._get_worker().batch_done.connect(self.controller.refresh_panels)

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
        return self.controller.raise_panel(p)  # type: ignore[attr-defined]


    def message(self, title, body) -> None:
        return self.controller.message(title, body)


    def status_message(self, message: str, kind: str = 'info', duration: int = 3000) -> None:
        return self.controller.status_message(message, kind, duration)


    def navigate_list(self, direction: str) -> None:
        return self.controller.navigate_list(direction)


    def mark_and_advance(self) -> None:
        return self.controller.mark_and_advance()


    def delegate_to_list(self, method: str, **kwargs: object) -> None:
        return self.controller.delegate_to_list(method, **kwargs)


    def delegate_to_thread(self, method: str, **kwargs: object) -> None:
        return self.controller.delegate_to_thread(method, **kwargs)


    def toggle_tag_hotkey(self, key: str) -> None:
        return self.controller.toggle_tag_hotkey(key)


    def add_panel(self, p: panel.Panel, focus: bool=True) -> None:
        return self.controller.add_panel(p, focus=focus)  # type: ignore[arg-type]


    def next_panel(self) -> None:
        return self.controller.next_panel()


    def previous_panel(self) -> None:
        return self.controller.previous_panel()


    def close_panel(self, to_close: int|panel.Panel|None=None) -> None:
        return self.controller.close_panel(to_close)  # type: ignore[arg-type]


    def open_search(self, query: str, keep_open: bool=False) -> None:
        return self.controller.open_search(query, keep_open=keep_open)


    def open_thread(self, thread_id: str, query: str) -> None:
        return self.controller.open_thread(thread_id, query)


    def open_compose(self, mode: str='', msg: Optional[dict]=None) -> None:
        return self.controller.open_compose(mode, msg)


    def open_tags(self, keep_open: bool=False) -> None:
        return self.controller.open_tags(keep_open=keep_open)


    def search_bar(self) -> None:
        return self.controller.search_bar()


    def edit_search_query(self) -> None:
        return self.controller.edit_search_query()


    def tag_bar(self, mode: Literal['tag', 'tag marked']='tag') -> None:
        return self.controller.tag_bar(mode)  # type: ignore[arg-type]


    def sync_mail(self, quiet: bool = True) -> None:
        return self.controller.sync_mail(quiet=quiet)  # type: ignore[attr-defined]

    def apply_filter_rules(self) -> None:
        return self.controller.apply_filter_rules()


    def expunge_trash(self) -> None:
        return self.controller.expunge_trash()


    def num_panels(self) -> int:
        return self.controller.num_panels()


    def refresh_tab_titles(self) -> None:
        return self.controller.refresh_tab_titles()


    def refresh_panels(self) -> None:
        return self.controller.refresh_panels()


    def update_single_thread(self, thread_id: str, msg_id: str|None=None):
        return self.controller.update_single_thread(thread_id, msg_id=msg_id)


    def _cleanup_sync(self) -> None:
        return self.controller._cleanup_sync()  # type: ignore[attr-defined]

    def prompt_quit(self) -> None:
        return self.controller.prompt_quit()


    def _save_open_searches(self) -> None:
        return self.controller._save_open_searches()


    def _restore_open_searches(self) -> None:
        return self.controller._restore_open_searches()


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
