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
from PyQt6.QtCore import *
from PyQt6.QtWidgets import *
from PyQt6.QtGui import QIcon, QCloseEvent
import logging
import os
from typing import Optional

from . import app
from . import commandbar
from . import panel
from . import settings
from . import themes

logger = logging.getLogger(__name__)


def _position_to_orientation(
    position: str,
) -> tuple[Qt.Orientation, bool]:
    """Return (splitter_orientation, list_is_first) for a pane position."""
    if position == 'right':
        return Qt.Orientation.Horizontal, True
    elif position == 'left':
        return Qt.Orientation.Horizontal, False
    elif position == 'below':
        return Qt.Orientation.Vertical, True
    else:  # 'above'
        return Qt.Orientation.Vertical, False


class MainWindow(QMainWindow):
    def __init__(self, a: app.Dodo):
        super().__init__()
        conf = QSettings('lazarus', 'lazarus')
        self.app = a

        # Try XDG theme first (picks up ~/.local/share/icons if installed),
        # fall back to the bundled 1024px PNG shipped in the package.
        icon = QIcon.fromTheme('lazarus')
        if icon.isNull():
            bundled = os.path.join(os.path.dirname(__file__),
                                   'icons', 'hicolor', '1024x1024',
                                   'apps', 'lazarus.png')
            if os.path.exists(bundled):
                icon = QIcon(bundled)
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.setWindowTitle("Lazarus")

        w = QWidget(self)
        w.setLayout(QVBoxLayout())
        self.setCentralWidget(w)
        w.layout().setContentsMargins(0, 0, 0, 0)
        w.layout().setSpacing(0)
        self.resize(1600, 800)

        geom = conf.value("main_window_geometry")
        if geom:
            self.restoreGeometry(geom)

        # -- Splitter: list tabs | thread preview ---------------------------
        orientation, list_first = _position_to_orientation(
            settings.thread_pane_position)
        self.main_splitter = QSplitter(orientation)
        self.main_splitter.setChildrenCollapsible(False)
        w.layout().addWidget(self.main_splitter, stretch=1)

        # List side: tabs (searches, compose, tags)
        self.tabs = QTabWidget()
        self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Thread preview side
        self.thread_container = QStackedWidget()
        self.thread_container.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._thread_placeholder = QLabel(
            "Select a thread to view  ·  ? for help")
        self._thread_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thread_placeholder.setStyleSheet(
            f'color: {settings.theme["fg_dim"]}; '
            f'font-family: {settings.search_font}; '
            f'font-size: {settings.search_font_size}pt;')
        self.thread_container.addWidget(self._thread_placeholder)
        self._active_thread: Optional[panel.Panel] = None

        # Add to splitter in correct order
        if list_first:
            self.main_splitter.addWidget(self.tabs)
            self.main_splitter.addWidget(self.thread_container)
        else:
            self.main_splitter.addWidget(self.thread_container)
            self.main_splitter.addWidget(self.tabs)

        # Restore splitter state
        self._restore_splitter_state()
        self.main_splitter.splitterMoved.connect(self._save_splitter_state)

        # Tab focus tracking
        def panel_focused(i: int) -> None:
            logger.info('Focusing panel %d', i)
            pw = self.tabs.widget(i)
            if pw and isinstance(pw, panel.Panel):
                if pw in self.app.panel_history:
                    self.app.panel_history.remove(pw)
                self.app.panel_history.append(pw)
                pw.setFocus()

        self.tabs.currentChanged.connect(panel_focused)
        self.show()

        # -- Command bar ----------------------------------------------------
        command_area = QWidget(self)
        command_label = QLabel("search", command_area)
        self.command_bar = commandbar.CommandBar(
            self.app, command_label, command_area)
        self.command_bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        command_area.setLayout(QHBoxLayout())
        command_area.layout().addWidget(command_label)
        command_area.layout().addWidget(self.command_bar)

        w.layout().addWidget(command_area)
        command_area.setVisible(False)

        # -- Status bar -----------------------------------------------------
        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setVisible(False)
        self.status_label.setContentsMargins(8, 2, 8, 2)
        self.status_label.setFixedHeight(24)
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        w.layout().addWidget(self.status_label)

        self.status_timer = QTimer()
        self.status_timer.setSingleShot(True)
        self.status_timer.timeout.connect(
            lambda: self.status_label.setVisible(False))

    # -- splitter persistence -----------------------------------------------

    def _save_splitter_state(self) -> None:
        conf = QSettings('lazarus', 'lazarus')
        key = f"main_splitter_state_{settings.thread_pane_position}"
        conf.setValue(key, self.main_splitter.saveState())

    def _restore_splitter_state(self) -> None:
        conf = QSettings('lazarus', 'lazarus')
        key = f"main_splitter_state_{settings.thread_pane_position}"
        state = conf.value(key)
        if state:
            self.main_splitter.restoreState(state)
        else:
            # Sensible default: list gets ~55% of available space
            total = (self.width() if self.main_splitter.orientation()
                     == Qt.Orientation.Horizontal else self.height())
            self.main_splitter.setSizes(
                [int(total * 0.55), int(total * 0.45)])

    # -- thread preview pane ------------------------------------------------

    def show_thread(self, thread_panel: panel.Panel) -> None:
        """Replace the thread preview with *thread_panel* and focus it."""
        # Detach previous thread panel
        if self._active_thread is not None:
            self._active_thread.before_close()
            idx = self.thread_container.indexOf(self._active_thread)
            if idx >= 0:
                self.thread_container.removeWidget(self._active_thread)
            self._active_thread.deleteLater()
            self._active_thread = None

        self.thread_container.show()
        self._active_thread = thread_panel
        self.thread_container.addWidget(thread_panel)
        self.thread_container.setCurrentWidget(thread_panel)
        thread_panel.has_refreshed.connect(self._on_thread_refreshed)
        thread_panel.setFocus()
        self._save_preview_state(hidden=False)

    def _on_thread_refreshed(self) -> None:
        """Update window title when the active thread refreshes."""
        pass  # title updates handled by refresh_tab_titles / panel itself

    def focus_list(self) -> None:
        """Move keyboard focus back to the current list tab."""
        w = self.tabs.currentWidget()
        if w:
            w.setFocus()

    def has_thread_preview(self) -> bool:
        """Return True if a thread is currently shown in the preview pane."""
        return self._active_thread is not None

    def active_thread(self) -> Optional[panel.Panel]:
        """Return the currently displayed thread panel, or None."""
        return self._active_thread

    def clear_thread(self) -> None:
        """Remove the current thread preview and show the placeholder."""
        if self._active_thread is not None:
            self._active_thread.before_close()
            idx = self.thread_container.indexOf(self._active_thread)
            if idx >= 0:
                self.thread_container.removeWidget(self._active_thread)
            self._active_thread.deleteLater()
            self._active_thread = None
        self.thread_container.hide()
        self._save_preview_state(hidden=True)
        self.focus_list()

    def _save_preview_state(self, hidden: bool) -> None:
        """Persist whether the thread preview is collapsed."""
        QSettings('lazarus', 'lazarus').setValue('preview_hidden', hidden)

    # -- status bar ---------------------------------------------------------

    def show_status(self, message: str, kind: str = 'info',
                    duration: int = 3000) -> None:
        """Show a transient status message at the bottom of the window."""
        colors = {
            'info': settings.theme.get('fg_good', settings.theme['fg']),
            'warning': settings.theme.get('fg_bad', settings.theme['fg']),
            'error': settings.theme['fg_bad'],
        }
        color = colors.get(kind, settings.theme['fg'])
        self.status_label.setStyleSheet(
            f'background-color: {settings.theme["bg_alt"]}; '
            f'color: {color}; '
            f'font-family: {settings.search_font}; '
            f'font-size: {settings.search_font_size}pt;'
        )
        self.status_label.setText(message)
        self.status_label.setVisible(True)
        logger.info('[%s] %s', kind, message)
        if duration > 0:
            self.status_timer.start(duration)
        else:
            self.status_timer.stop()

    # -- close --------------------------------------------------------------

    def before_close_all(self) -> bool:
        """Run before_close on all panels.  Returns False if any cancelled."""
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, panel.Panel) and not w.before_close():
                return False
        if self._active_thread is not None:
            if not self._active_thread.before_close():
                return False
        return True

    def closeEvent(self, e: QCloseEvent) -> None:
        conf = QSettings('lazarus', 'lazarus')
        conf.setValue("main_window_geometry", self.saveGeometry())
        if self.before_close_all():
            e.accept()
        else:
            e.ignore()
