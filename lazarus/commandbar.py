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
from typing import Dict, List, Tuple, Optional, Callable, Any
from PyQt6.QtWidgets import QCompleter, QLabel, QPlainTextEdit, QWidget
from PyQt6.QtGui import QKeyEvent, QTextCursor, QTextOption
from PyQt6 import QtCore

from . import util
from . import keymap
from . import notmuch
from .protocols import PanelApp

class _TagLoader(QtCore.QThread):
    """Load the notmuch tag list in the background.

    ``notmuch.tags()`` spawns a subprocess (~150ms) and the command bar
    is constructed at startup, so the list is fetched off the UI thread
    (mirrors ``_AddressLoader`` for the address book).
    """

    loaded = QtCore.pyqtSignal(list)

    def run(self) -> None:
        try:
            tags = notmuch.tags()
        except Exception:
            tags = []
        self.loaded.emit(tags)


class _TagStore(QtCore.QObject):
    """Process-wide manager for the tag-loading thread.

    The command bar is constructed at startup and can be destroyed at any
    time (window close, tests); a ``_TagLoader`` parented to the bar would
    then be deleted mid-run and abort with "QThread: Destroyed while
    thread is still running". The store outlives every ``CommandBar`` and
    owns the loader: a fetch is started at most once per idle period, the
    result is broadcast to every subscribed bar (connections to destroyed
    bars are dropped by Qt automatically), and a finished loader is only
    replaced -- never destroyed while running.
    """

    loaded = QtCore.pyqtSignal(list)

    def __init__(self) -> None:
        super().__init__()
        self._loader: Optional[_TagLoader] = None

    def ensure_loaded(self) -> None:
        """Start a background fetch unless one is already in flight.

        Tags change as mail syncs, so a later ``CommandBar`` re-fetches
        once the previous loader has finished; the result always lands
        asynchronously via :attr:`loaded` (never a blocking call).
        """
        if self._loader is not None and self._loader.isRunning():
            return
        self._loader = _TagLoader()
        self._loader.loaded.connect(self._on_loaded)
        self._loader.start()

    def _on_loaded(self, tags: list) -> None:
        self.loaded.emit(tags)


# One store for the process: keeps the loader thread alive across every
# CommandBar that comes and goes.
_tag_store = _TagStore()


class CommandBar(QPlainTextEdit):
    """A command bar that opens as a centered modal overlay when searching
    or tagging.

    The entry is a wrapping multi-line editor whose size is driven by its
    content (via :attr:`refit`): the container grows with the query up to
    the window width, then wraps to additional lines.
    """

    def __init__(self, a: PanelApp, label: QLabel, parent: QWidget,
                 overlay: Optional[QWidget] = None):
        super().__init__(parent)
        self.app = a
        self.label = label
        # The full-window dim layer to show/hide when the bar opens/closes.
        # Kept separate from the widget parent chain because the entry is
        # re-parented into the bar's styled container by its layout.
        self.overlay = overlay
        self.mode = ''
        self.history: Dict[str, Tuple[int, List[str]]] = {}
        self.callback: Optional[Callable[[str], Any]] = None

        # Wrapping multi-line entry, sized externally to fit its content.
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumSize(40, 30)

        # Called by the owner to resize the entry/container to content.
        self.refit: Optional[Callable[[], None]] = None
        self.textChanged.connect(self._refit)

        self.completer = self._get_completer()

    def _refit(self) -> None:
        """Ask the owner to re-size the bar to its current content."""
        if self.refit is not None:
            self.refit()

    def _cursor_to_end(self) -> None:
        """Move the edit cursor to the end of the text."""
        c = self.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(c)

    def _get_completer(self) -> QCompleter:
        """Prepare the completer for tags (loaded in the background) and
        theme names (available immediately -- `themes.REGISTRY` is built
        at startup, before `MainWindow`/`CommandBar` are constructed)."""
        completer = QCompleter(self)
        self._tag_model = QtCore.QStringListModel(completer)
        self._theme_model = QtCore.QStringListModel(completer)
        from . import themes
        self._theme_model.setStringList(sorted(themes.REGISTRY.keys()))
        completer.setModel(self._tag_model)
        completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
        completer.setModelSorting(QCompleter.ModelSorting.UnsortedModel)
        completer.setWidget(self)
        completer.activated.connect(self.handleCompletion)

        # QPlainTextEdit.textChanged carries no payload (unlike QLineEdit).
        self.textChanged.connect(
            lambda: self.handleTextChanged(self.toPlainText()))

        # Tags load asynchronously, managed process-wide (see _TagStore):
        # subscribe to the shared 'loaded' signal; the model starts empty
        # and populates when the fetch lands (~150ms).
        _tag_store.loaded.connect(self._on_tags_loaded)
        _tag_store.ensure_loaded()
        return completer

    def _on_tags_loaded(self, tags: list) -> None:
        """Populate the completer once the background tag fetch lands."""
        self._tag_model.setStringList(tags)

    def handleTextChanged(self, text: str) -> None:
        """Open suggestion dialog if a matching tag or theme name is
        present. 'theme' mode completes against the whole line (there's
        no '+'/'tag:' token structure to parse out, unlike tag entry);
        every other mode completes tag tokens as before."""
        if self.mode == 'theme':
            prefix = text
            model: QtCore.QStringListModel = self._theme_model
        else:
            prefix = text.rsplit(sep=" ", maxsplit=1)[-1]
            if len(prefix) == 0:
                return
            elif prefix[0] in ["+", "-"]:
                prefix = prefix[1:]
            elif prefix[:4] == "tag:":
                prefix = prefix[4:]
            else:
                prefix = ""
            model = self._tag_model

        if len(prefix) > 0:
            if self.completer.model() is not model:
                self.completer.setModel(model)
            self.completer.setCompletionPrefix(prefix)

            popup = self.completer.popup()
            cmodel = self.completer.completionModel()
            if popup is not None and cmodel is not None:
                popup.setCurrentIndex(cmodel.index(0, 0))
            self.completer.complete()

    def handleCompletion(self, text: str) -> None:
        """Use the choosen tag."""
        prefix = self.completer.completionPrefix()
        self.setPlainText(self.toPlainText()[:-len(prefix)] + text + " ")
        # setPlainText resets the cursor to the start; place it at the end
        # so the user can keep typing another term (e.g. ' and …').
        self._cursor_to_end()

    def open(self, mode: str, callback: Callable[[str], Any]) -> None:
        """Open the command bar and give it focus

        This method sets the `command_area` QWidget (which contains the command bar and
        its label) to be visible, and sets the `command_label` to be equal to `mode`.

        :param mode: a string used to set the label next to the command bar and
                     keep a unique command history (e.g. 'search' history should be
                     different from 'tag' history).
        :param callback: a function called with the user input to run the command
        """

        self.mode = mode
        self.callback = callback
        self.label.setText(mode)

        target = self.overlay if self.overlay is not None else self.parent()
        if isinstance(target, QWidget): target.setVisible(True)

        self.setFocus()
        self._refit()

    def close_bar(self) -> None:
        """Clear the command and close

        Call this method by itself to cancel the command and close the bar. Note we use
        `close_bar` to avoid a name clash with QWidget.close."""

        if self.mode in self.history:
            _, h = self.history[self.mode]
            self.history[self.mode] = (len(h), h)

        self.setPlainText('')
        target = self.overlay if self.overlay is not None else self.parent()
        if isinstance(target, QWidget): target.setVisible(False)

        w = self.app.tabs.currentWidget()
        if w: w.setFocus()

    def accept(self) -> None:
        """Apply the command typed into the command bar and close

        After the command has been applied, this method saves the command to the command
        history associated with the current mode, then calls :func:`close_bar` to clear
        the command and close the command bar."""

        popup = self.completer.popup()
        if popup is not None and popup.isVisible():
            return

        if self.callback:
            self.callback(self.toPlainText())

        if self.mode in self.history:
            pos, h = self.history[self.mode]
            h.append(self.toPlainText())
            self.history[self.mode] = (pos + 1, h)
        else:
            self.history[self.mode] = (1, [self.toPlainText()])

        self.close_bar()

    def history_previous(self) -> None:
        """Cycle to the previous command in the command history

        Note a separate history is kept for each mode."""

        if self.mode in self.history:
            pos, h = self.history[self.mode]
            if len(h) != 0:
                pos = max(pos - 1, 0)
                self.history[self.mode] = (pos, h)
                self.setPlainText(h[pos])
                self._cursor_to_end()

    def history_next(self) -> None:
        """Cycle to the next command in the command history

        Note a separate history is kept for each mode."""

        if self.mode in self.history:
            pos, h = self.history[self.mode]
            if len(h) != 0:
                pos = min(pos + 1, len(h) - 1)
                self.history[self.mode] = (pos, h)
                self.setPlainText(h[pos])
                self._cursor_to_end()

    def keyPressEvent(self, e: QKeyEvent | None) -> None:
        """Process keyboard input while the command bar is in focus.

        Translate the key event into a string with :func:`~lazarus.util.key_string`
        and check if it is in :func:`~lazarus.keymap.command_bar_keymap`. If it is,
        fire the associated function. Otherwise, pass the event on to the text
        box.

        Note: Key chords are NOT supported in the command bar.
        """
        if e is None:
            return
        popup = self.completer.popup()
        if popup is not None and popup.isVisible() and e.key() in [
            QtCore.Qt.Key.Key_Enter,
            QtCore.Qt.Key.Key_Return,
            QtCore.Qt.Key.Key_Up,
            QtCore.Qt.Key.Key_Down,
            QtCore.Qt.Key.Key_Tab,
            QtCore.Qt.Key.Key_Backtab,
            QtCore.Qt.Key.Key_Escape,
        ]:
            # ignore keymaps if the popup is shown!
            e.ignore()
            return

        k = util.key_string(e)
        if k in keymap.command_bar_keymap:
            keymap.command_bar_keymap[k][1](self)
        else:
            super().keyPressEvent(e)
