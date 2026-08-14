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
from PyQt6.QtGui import QIcon, QCloseEvent, QColor, QMouseEvent, QPalette, QResizeEvent, QShowEvent
import logging
import math
import os
from typing import Callable, Optional

from . import app
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .controller import AppController
    from .app import Dodo
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


class SearchOverlay(QWidget):
    """Full-window dim layer hosting the centered command bar.

    Clicking anywhere on the dim dismisses the bar (rofi-style); clicks on
    the entry box itself are swallowed by the box. The overlay is a child
    of the central widget (not in a layout) so it floats above the
    splitter; it is kept sized to the window on resize and re-raised on
    show so it stays above the web view.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._dim_click_handler: Optional[Callable[[], None]] = None

    def set_dim_click_handler(self, fn: Callable[[], None]) -> None:
        self._dim_click_handler = fn

    def mousePressEvent(self, e: QMouseEvent | None) -> None:
        # Click-away dismiss. Only clicks on the dim land here; the entry
        # and its container accept their own mouse events.
        if e is None:
            return
        if self._dim_click_handler is not None:
            self._dim_click_handler()
        e.accept()

    def showEvent(self, e: QShowEvent | None) -> None:
        super().showEvent(e)
        p = self.parentWidget()
        if p is not None:
            self.setGeometry(p.rect())
        self.raise_()
        # Deferred raise beats any late compositor/Chromium surface
        # stacking when the overlay opens over the web view.
        QTimer.singleShot(0, self.raise_)


class _BarBox(QFrame):
    """Rounded container for the mode label + entry.

    Accepts mouse presses so clicks on the box padding don't fall through
    to the dim (which would dismiss the bar).
    """

    def mousePressEvent(self, e: QMouseEvent | None) -> None:
        if e is not None:
            e.accept()


class MainWindow(QMainWindow):
    def __init__(self, a: "Dodo | AppController"):
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
        self.tabs.setMinimumWidth(220)
        self.tabs.setMinimumHeight(220)
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

        # Remember the last "open" splitter position (when preview was
        # visible) so the next email open restores the user's divider at
        # ~50/50 or wherever they dragged it — but always *start* with the
        # preview collapsed so the list gets full width (post-open state
        # but closed). The warm view stays alive so first open has no
        # Chromium cold-start flicker.
        self._open_splitter_state: bytes | None = None
        self._restore_splitter_state()
        # Save the open-state for later restores (either from QSettings or
        # the default 50/50). Then force collapsed on startup per pref.
        try:
            conf2 = QSettings('lazarus', 'lazarus')
            saved = conf2.value(f"main_splitter_state_{settings.thread_pane_position}")
            if saved:
                # QSettings may return QByteArray or bytes depending on Qt ver
                self._open_splitter_state = bytes(saved) if isinstance(saved, (bytes, bytearray)) else saved
            else:
                # No saved state yet — use the default 50/50 as open state
                self._open_splitter_state = bytes(self.main_splitter.saveState())
        except Exception:
            try:
                self._open_splitter_state = bytes(self.main_splitter.saveState())
            except Exception:
                self._open_splitter_state = None
        self.thread_container.hide()
        try:
            total = self.width() if self.main_splitter.orientation() == Qt.Orientation.Horizontal else self.height()
            if list_first:
                self.main_splitter.setSizes([total, 0])
            else:
                self.main_splitter.setSizes([0, total])
        except Exception:
            pass
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

        # -- Command bar (centered modal overlay) ---------------------------
        # The search/tag/edit-query bar opens as a modal overlay dead-center
        # in the window: a dimmed full-window layer with the entry box on
        # top, launcher-style. Clicking the dim dismisses; Esc still works.
        command_area = SearchOverlay(w)
        command_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Dim layer: black at partial alpha (works on light + dark themes;
        # a theme-bg dim would be invisible against a theme-bg window).
        dim = QColor(0, 0, 0)
        dim.setAlphaF(0.45)
        dim_pal = command_area.palette()
        dim_pal.setColor(QPalette.ColorRole.Window, dim)
        command_area.setPalette(dim_pal)
        command_area.setAutoFillBackground(True)

        command_label = QLabel("search", command_area)
        command_label.setStyleSheet(
            f'QLabel {{ color: {settings.theme["fg_dim"]}; '
            f'font-family: {settings.search_font}; '
            f'font-size: {settings.search_font_size}pt; '
            f'padding-left: 14px; }}')

        self.command_bar = commandbar.CommandBar(
            self.app, command_label, command_area, overlay=command_area)
        self.command_bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.command_bar.setFrameShape(QFrame.Shape.NoFrame)
        self.command_bar.setViewportMargins(0, 6, 14, 6)
        self.command_bar.setStyleSheet(
            f'QPlainTextEdit {{ background: transparent; border: none; '
            f'color: {settings.theme["fg"]}; '
            f'font-family: {settings.search_font}; '
            f'font-size: {settings.search_font_size}pt; '
            f'selection-background-color: '
            f'{settings.theme.get("bg_button", settings.theme["fg_dim"])}; '
            f'selection-color: {settings.theme["fg"]}; }}')

        # Rounded box holding the label + entry, with a soft drop shadow.
        # Width/height are driven by _reflow_command_bar to fit content.
        box = _BarBox(command_area)
        box.setObjectName('command_box')
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box.setStyleSheet(
            f'QFrame#command_box {{ '
            f'background-color: {settings.theme.get("bg_alt", settings.theme["bg"])}; '
            f'border: 1px solid {settings.theme.get("bg_button", settings.theme["fg_dim"])}; '
            f'border-radius: 10px; }}')
        box_lay = QHBoxLayout(box)
        box_lay.setContentsMargins(0, 0, 0, 0)
        box_lay.setSpacing(6)
        box_lay.addWidget(command_label)
        box_lay.addWidget(self.command_bar)

        shadow = QGraphicsDropShadowEffect(box)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 130))
        box.setGraphicsEffect(shadow)

        overlay_lay = QVBoxLayout(command_area)
        overlay_lay.setContentsMargins(0, 0, 0, 0)
        overlay_lay.addStretch(1)
        overlay_row = QHBoxLayout()
        overlay_row.addStretch(1)
        overlay_row.addWidget(box)
        overlay_row.addStretch(1)
        overlay_lay.addLayout(overlay_row)
        overlay_lay.addStretch(1)

        command_area.set_dim_click_handler(self.command_bar.close_bar)
        command_area.setVisible(False)
        self.command_area = command_area
        self._command_box = box
        self.command_bar.refit = self._reflow_command_bar
        self._reflow_command_bar()

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

    # -- command bar sizing -------------------------------------------------

    def _reflow_command_bar(self) -> None:
        """Size the command bar to its content.

        The box grows with the widest line of text, capped at the window
        width (minus side margins); beyond that the entry wraps and the
        box grows vertically by line count.
        """
        bar = self.command_bar
        overlay = self.command_area
        box = getattr(self, '_command_box', None)
        if box is None:
            return

        fm = bar.fontMetrics()
        margins = bar.viewportMargins()
        lm, tm, rm, bm = margins.left(), margins.top(), margins.right(), margins.bottom()
        doc_m = bar.document().documentMargin()
        text = bar.toPlainText()
        lines = text.split('\n')

        label_w = bar.label.sizeHint().width()
        spacing = 6
        border = 2  # 1px QSS border on each side of the box
        side_margin = 24
        caret = fm.horizontalAdvance('M')

        max_advance = max(
            (fm.horizontalAdvance(l) for l in lines), default=0)
        # Horizontal space the text needs (text + margins + caret room).
        content_need = max_advance + 2 * doc_m + lm + rm + caret

        max_box_w = max(320, overlay.width() - 2 * side_margin)
        entry_w = int(max(60, min(content_need,
                                 max_box_w - label_w - spacing - border)))
        box_w = int(label_w + spacing + entry_w + border)
        box_w = max(320, min(box_w, max_box_w))
        entry_w = box_w - label_w - spacing - border

        # Wrapped line count (exact for the mono default font).
        content_w = max(40, entry_w - lm - rm - 2 * doc_m)
        total_lines = 0
        for l in lines:
            a = fm.horizontalAdvance(l)
            total_lines += max(1, math.ceil(a / content_w))

        entry_h = int(total_lines * fm.height() + 2 * doc_m + tm + bm + 2)
        bar.setFixedWidth(entry_w)
        bar.setFixedHeight(entry_h)
        box.setFixedWidth(box_w)
        box.setFixedHeight(entry_h + border)

    # -- splitter persistence -----------------------------------------------

    def _save_splitter_state(self) -> None:
        # When preview is collapsed we don't want to persist [total, 0]
        # as the "open" position — keep the last open ratio instead.
        if self._active_thread is None and self.thread_container.isHidden():
            return
        conf = QSettings('lazarus', 'lazarus')
        key = f"main_splitter_state_{settings.thread_pane_position}"
        conf.setValue(key, self.main_splitter.saveState())
        # Keep the in-memory open-state in sync while preview is visible
        try:
            self._open_splitter_state = bytes(self.main_splitter.saveState())
        except Exception:
            pass

    def _restore_splitter_state(self) -> None:
        conf = QSettings('lazarus', 'lazarus')
        key = f"main_splitter_state_{settings.thread_pane_position}"
        state = conf.value(key)
        if state:
            self.main_splitter.restoreState(state)
        else:
            # Default open ratio: ~50/50 when preview is visible
            total = (self.width() if self.main_splitter.orientation()
                     == Qt.Orientation.Horizontal else self.height())
            self.main_splitter.setSizes(
                [int(total * 0.50), int(total * 0.50)])

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

        # If we started collapsed, restore the last open divider position
        # (either QSettings or the default 50/50 captured at startup).
        was_hidden = self.thread_container.isHidden()
        self.thread_container.show()
        if was_hidden and getattr(self, '_open_splitter_state', None):
            try:
                self.main_splitter.restoreState(self._open_splitter_state)  # type: ignore[arg-type]
            except Exception:
                pass
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

    def resizeEvent(self, e: QResizeEvent | None) -> None:
        """Keep the command-bar overlay covering the whole window."""
        super().resizeEvent(e)
        area = getattr(self, 'command_area', None)
        cw = self.centralWidget()
        if area is not None and cw is not None:
            area.setGeometry(cw.rect())
        # Max width of the bar depends on window width.
        if area is not None and area.isVisible():
            self._reflow_command_bar()

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

    def closeEvent(self, e: QCloseEvent | None) -> None:
        conf = QSettings('lazarus', 'lazarus')
        conf.setValue("main_window_geometry", self.saveGeometry())
        if e is None or self.before_close_all():
            if e is not None:
                e.accept()
        else:
            e.ignore()
