#     Dodo - A graphical, hackable email client based on notmuch
#     Copyright (C) 2021 - Aleks Kissinger
#
# This file is part of Dodo
#
# Dodo is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Dodo is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Dodo. If not, see <https://www.gnu.org/licenses/>.

"""Address autocomplete powered by ``notmuch address``.

:class:`AddressCompleter` is a :class:`~PyQt6.QtWidgets.QCompleter`
subclass that works exactly like the tag completer in
:mod:`lazarus.commandbar`: load every address once (in a background thread,
since ``notmuch address`` scans messages and takes a few seconds), shove
them into the model, then let Qt's built-in ``MatchContains`` filter do
the rest — instant results, no Python filtering, no per-keystroke
notmuch calls.
"""

from __future__ import annotations
import subprocess
import json
import logging
from typing import Optional

from PyQt6.QtCore import (
    Qt, QStringListModel, QThread, QObject, pyqtSignal,
)
from PyQt6.QtWidgets import QCompleter, QLineEdit

from . import notmuch

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Minimum characters before the popup appears
# ---------------------------------------------------------------------------

_MIN_CHARS = 2


# ---------------------------------------------------------------------------
# Background thread: load all addresses once
# ---------------------------------------------------------------------------

class _AddressLoader(QThread):
    """Runs ``notmuch address ... '*'`` once and emits the parsed list."""

    loaded = pyqtSignal(list)

    def run(self) -> None:
        try:
            r = notmuch.run(
                'address', '--output=recipients',
                '--deduplicate=address', '--format=json',
                '--', '*',
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning('notmuch address full load failed: %s', e)
            self.loaded.emit([])
            return

        if r.returncode != 0:
            logger.warning('notmuch address returned %d: %s',
                           r.returncode, r.stderr.strip()[:200])
            self.loaded.emit([])
            return

        try:
            results = json.loads(r.stdout)
        except json.JSONDecodeError:
            logger.warning('notmuch address: bad JSON output')
            self.loaded.emit([])
            return

        # notmuch --format=json returns:
        #   [{"name": "...", "address": "...", "name-addr": "..."}, ...]
        # Use the pre-formatted "name-addr" field directly.
        addresses: list[str] = []
        seen: set[str] = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            formatted = item.get('name-addr', '') or item.get('address', '')
            if not formatted:
                continue
            key = formatted.casefold()
            if key not in seen:
                seen.add(key)
                addresses.append(formatted)

        logger.info('Loaded %d addresses from notmuch', len(addresses))
        self.loaded.emit(addresses)


# ---------------------------------------------------------------------------
# Shared loader — started once, shared by all AddressCompleter instances
# ---------------------------------------------------------------------------

_shared_addresses: list[str] = []
"""All addresses, shared across all completer instances.  Empty until
the loader thread emits its :attr:`_AddressLoader.loaded` signal.
The model is updated from that signal on the UI thread, so reads from
:meth:`AddressCompleter._on_text_changed` are always safe."""

_shared_loader: _AddressLoader | None = None
"""The (singleton) background loader thread."""

def preload_addresses() -> None:
    """Start loading the address book in the background.

    Safe to call multiple times — the loader only runs once per session.
    Call this at application startup (or when the first compose panel is
    created) so addresses are ready by the time the user types in the To
    field.
    """
    global _shared_loader
    if _shared_loader is not None:
        return

    loader = _AddressLoader()
    _shared_loader = loader

    def _on_loaded(addresses: list[str]) -> None:
        global _shared_addresses, _shared_loader
        _shared_addresses = addresses
        _shared_loader = None
        loader.deleteLater()

    loader.loaded.connect(_on_loaded)
    # Safety net: if loaded never fires (e.g. timeout/error), still clean up.
    loader.finished.connect(loader.deleteLater)
    loader.start()


# ---------------------------------------------------------------------------
# AddressCompleter
# ---------------------------------------------------------------------------

class AddressCompleter(QCompleter):
    """A QCompleter backed by notmuch's address index.

    Unlike the tag completer in :class:`lazarus.commandbar.CommandBar`
    (which is its own QLineEdit), we complete addresses in an existing
    QLineEdit.  Because ``QLineEdit.setCompleter()`` forces inline
    completion that can't be suppressed, we do NOT attach the completer
    to the line edit.  Instead we manually filter, show the popup, and
    replace only the active token when the user picks a suggestion —
    exactly the UX CommandBar provides for tags.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)

        self._model = QStringListModel()
        self.setModel(self._model)
        self.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setModelSorting(QCompleter.ModelSorting.UnsortedModel)
        self.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.setMaxVisibleItems(8)

        self._line_edit: QLineEdit | None = None
        self._current_prefix = ''

    def set_line_edit(self, widget: QLineEdit) -> None:
        """Wire up this completer to *widget* without attaching it
        via ``QLineEdit.setCompleter()``, which forces inline completion.

        We call ``setWidget()`` directly so the completer knows where
        to show its popup, but skip ``widget.setCompleter()`` to avoid
        QLineEdit's internal inline-completion logic.
        """
        self._line_edit = widget
        self.setWidget(widget)
        widget.textChanged.connect(self._on_text_changed)
        self.activated.connect(self._on_activated)

    def _on_activated(self, text: str) -> None:
        """Replace only the active token with the chosen completion."""
        if self._line_edit is None:
            return
        prefix = self._current_prefix
        current = self._line_edit.text()
        idx = current.rfind(prefix)
        if idx >= 0:
            new_text = current[:idx] + text + ', '
            self._line_edit.setText(new_text)

    def _on_text_changed(self, text: str) -> None:
        """Filter the model and show/hide the popup.

        Identical pattern to
        :meth:`lazarus.commandbar.CommandBar.handleTextChanged`.
        """
        prefix = _extract_active_token(text)
        self._current_prefix = prefix

        if len(prefix) < _MIN_CHARS:
            self.popup().hide()
            return

        global _shared_addresses
        if not _shared_addresses:
            self.popup().hide()
            return

        # Filter in Python (instant on ~200 addresses).
        matches = [a for a in _shared_addresses
                   if prefix.casefold() in a.casefold()][:20]
        self._model.setStringList(matches)

        if not matches:
            self.popup().hide()
            return

        popup = self.popup()
        popup.setCurrentIndex(self.completionModel().index(0, 0))
        self.complete()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_active_token(text: str) -> str:
    """Return the address token the user is currently typing.

    In a comma-separated list like ``"Alice <a@b.com>, Bob"`` this
    returns ``"Bob"`` (the last token), stripped of whitespace and
    trailing angle brackets.
    """
    tokens = text.split(',')
    token = tokens[-1].strip()
    token = token.rstrip('>').strip()
    return token
