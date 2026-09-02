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

from PyQt6.QtCore import QSocketNotifier, QTimer, Qt
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineCore import QWebEngineUrlScheme
import sys
import signal
import fcntl
import subprocess
import logging

from . import settings
from . import themes
from . import helpwindow
from . import mainwindow
from . import actions
from . import address_completer
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
        # Must be set before the underlying QGuiApplication is constructed,
        # otherwise the first QWebEngineView forces a compositor surface
        # recreation (visible as a window flicker/"restart" on Wayland).
        # Qt docs: AA_ShareOpenGLContexts must be set before QApplication.
        try:
            from PyQt6.QtCore import Qt as _Qt
            QApplication.setAttribute(_Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
        except Exception:
            pass
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

        # construct help window lazily on first call to show_help()
        self.help_window: helpwindow.HelpWindow | None = None

        # Theme color map: auto-create ~/.config/lazarus/themes/colormap.py
        # on first run, then apply any per-theme overrides it defines.
        themes.load_colormap()

        # Build the theme registry (hand-written + bundled pack + user
        # packs + settings.theme_overrides) now that config.py has run,
        # then prefer a remembered live-switched theme over config.py's
        # default, if one exists and still resolves.
        themes.REGISTRY = themes.create_lazy_registry()
        resolved_theme = themes.resolve_initial_theme()

        # apply theme
        themes.apply_theme(resolved_theme)

        # register custom URL schemes used by embedded HTML viewer
        for proto in LOCAL_PROTOCOLS:
            scheme = QWebEngineUrlScheme(proto.encode('utf-8'))
            scheme.setSyntax(QWebEngineUrlScheme.Syntax.Path)
            QWebEngineUrlScheme.registerScheme(scheme)

        # set up GUI
        self.panel_history: list = []
        self.main_window = mainwindow.MainWindow(self)
        self.tabs = self.main_window.tabs
        self.command_bar = self.main_window.command_bar
        self.lastWindowClosed.connect(self.quit)

        # Controller owns panel registry + commands; Dodo keeps only the
        # app-lifecycle methods below (see the shim note near show_help).
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
        # Registered through actions (not a one-off connect against the
        # first worker instance) so a recreated worker keeps the wiring.
        actions.set_batch_done_listener(self.controller.refresh_panels)

        # Preload the address book in the background so autocomplete
        # is ready by the time the user opens the compose panel.
        address_completer.preload_addresses()

        # Warm the Chromium renderer process in the background on the first
        # event loop tick so startup is not blocked by process spawning.
        self._warm_view: object | None = None
        QTimer.singleShot(0, self._warm_webengine)

        # Handle Ctrl-C: use a pipe + QSocketNotifier so the Qt event loop
        # wakes up immediately when a Unix signal arrives.
        self._signal_read_fd, self._signal_write_fd = os.pipe()
        fcntl.fcntl(self._signal_read_fd, fcntl.F_SETFL, os.O_NONBLOCK)
        fcntl.fcntl(self._signal_write_fd, fcntl.F_SETFL, os.O_NONBLOCK)
        signal.set_wakeup_fd(self._signal_write_fd)

        self._signal_notifier = QSocketNotifier(
            self._signal_read_fd, QSocketNotifier.Type.Read, self)  # type: ignore[call-overload]
        # The stub declares the socket as voidptr; an int fd works at runtime.
        self._signal_notifier.activated.connect(self._handle_signal_wakeup)

        signal.signal(signal.SIGINT, lambda *_: None)

        # open init_queries and make un-closeable
        for query in settings.init_queries:
            self.controller.open_search(query, keep_open=True)

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
        if self.help_window is None:
            from . import helpwindow
            self.help_window = helpwindow.HelpWindow()
        self.help_window.show()

    # -- Dodo-owned app surface ---------------------------------------------
    # Everything the panels and keymap dispatch to lives on
    # ``AppController`` (see :class:`lazarus.protocols.PanelApp`) — panels
    # receive the controller at runtime.  Dodo keeps only the four methods
    # wired to Qt/QApplication lifecycle plumbing below (sync timer,
    # aboutToQuit, startup restore) plus ``show_help``.

    def sync_mail(self, quiet: bool = True) -> None:
        return self.controller.sync_mail(quiet=quiet)

    def _cleanup_sync(self) -> None:
        return self.controller._cleanup_sync()

    def _restore_open_searches(self) -> None:
        return self.controller._restore_open_searches()

    def _warm_webengine(self) -> None:
        """Pre-initialise Chromium and keep it warm.

        The first ``QWebEngineView`` in a Qt app lazily spawns the GPU +
        renderer subprocesses. On Wayland this can appear as a window
        flicker or "restart". A disposable view that is deleted after
        loadFinished goes cold again; instead we keep a hidden view alive
        as a child of the main window for the app lifetime, sharing the
        same ``cid``/``message`` scheme handlers as real ThreadPanels.
        """
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWebEngineCore import QWebEngineProfile
        from PyQt6.QtCore import QTimer
        from PyQt6.QtGui import QColor

        # Register custom schemes on this profile too (mirrors ThreadPanel)
        try:
            profile = QWebEngineProfile(self)
            # Reuse the same scheme registration as in Dodo.__init__ —
            # QWebEngineUrlScheme.registerScheme is idempotent.
            from .webengine import EmbeddedImageHandler, MessageHandler
            # Keep handlers alive as attributes so they are not GC'd
            self._warm_cid_handler = EmbeddedImageHandler(self)  # type: ignore[attr-defined]
            self._warm_msg_handler = MessageHandler(self)  # type: ignore[attr-defined]
            profile.installUrlSchemeHandler(b'cid', self._warm_cid_handler)  # type: ignore[attr-defined]
            profile.installUrlSchemeHandler(b'message', self._warm_msg_handler)  # type: ignore[attr-defined]
        except Exception:
            profile = None

        view = QWebEngineView(self.main_window)
        # Keep it in the widget tree but zero-sized / hidden so the
        # compositor has a stable surface and the renderer stays resident.
        view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        view.hide()
        view.setFixedSize(1, 1)
        view.move(-10, -10)
        if profile is not None:
            # Assign the shared profile's page
            from PyQt6.QtWebEngineCore import QWebEnginePage
            page = QWebEnginePage(profile, view)
            try:
                page.setBackgroundColor(QColor(settings.theme['bg']))
            except Exception:
                pass
            view.setPage(page)

        self._warm_view = view  # keep alive
        view.show()
        view.setHtml('<html><body></body></html>')
        # Fire-and-forget: the nested event loop (up to 5 s) used to
        # block startup until the first load finished. The renderer/GPU
        # processes spawn in the background and stay resident via this
        # tiny hidden view, so the first real email open is still warm —
        # without stalling Dodo.__init__. The hide is deferred so
        # Chromium has a chance to spawn its processes first.
        QTimer.singleShot(1000, view.hide)
        # Ensure the main window is the active, visible surface
        self.main_window.raise_()
        self.main_window.activateWindow()


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
    with open(desktop_path, 'w') as df:
        df.write(_DESKTOP_ENTRY)
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
