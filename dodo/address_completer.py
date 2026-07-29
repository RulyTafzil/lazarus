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
subclass backed by notmuch's address database.  On the first keystroke
it launches a background thread to fetch *all* known addresses via
``notmuch address ... '*'``, then filters the cached results in Python
for subsequent keystrokes — fast, predictable, and no per-prefix
notmuch timeouts.
"""

from __future__ import annotations
import subprocess
import json
import logging
from typing import Optional, List

from PyQt6.QtCore import (
    Qt, QStringListModel, QTimer, QThread, QObject, pyqtSignal,
)
from PyQt6.QtWidgets import QCompleter, QLineEdit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Minimum characters before autocomplete kicks in
# ---------------------------------------------------------------------------

_MIN_CHARS = 2
"""Minimum characters before the completer fires."""


# ---------------------------------------------------------------------------
# Background thread: load all addresses once
# ---------------------------------------------------------------------------

class _AddressLoader(QThread):
    """Runs ``notmuch address ... '*'`` in the background and emits the
    parsed result via :attr:`loaded`."""

    loaded = pyqtSignal(list)

    def run(self) -> None:
        try:
            r = subprocess.run(
                ['notmuch', 'address', '--output=recipients',
                 '--deduplicate=address', '--format=json',
                 '--', '*'],
                capture_output=True, text=True, timeout=60,
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
# AddressCompleter
# ---------------------------------------------------------------------------

class AddressCompleter(QCompleter):
    """A QCompleter backed by notmuch's address index.

    Usage::

        completer = AddressCompleter()
        to_field = QLineEdit()
        completer.set_line_edit(to_field)

    On the first keystroke the completer fetches every known address
    from notmuch in a background thread.  Once loaded, subsequent
    keystrokes filter the in-memory cache — no more notmuch calls.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._model = QStringListModel()
        self.setModel(self._model)
        self.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.setMaxVisibleItems(8)

        # Full address book, loaded once
        self._all_addresses: list[str] = []
        self._loaded = False

        # Background loader
        self._loader: _AddressLoader | None = None

        # Debounce timer
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._do_query)

        self._pending_prefix = ''

    def set_line_edit(self, widget: QLineEdit) -> None:
        """Attach this completer to *widget* and wire up input
        debouncing.  Call this instead of ``widget.setCompleter()``."""
        widget.setCompleter(self)
        widget.textEdited.connect(self._on_text_edited)

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def _on_text_edited(self, text: str) -> None:
        """Called whenever the user types in the attached QLineEdit.

        Extracts the active token (the last comma-separated segment)
        and schedules filtering after a 200 ms debounce.
        """
        prefix = _extract_active_token(text)
        if len(prefix) < _MIN_CHARS:
            self._model.setStringList([])
            self._pending_prefix = ''
            return

        self._pending_prefix = prefix

        # Start the background loader if this is the first keystroke
        if not self._loaded and self._loader is None:
            self._start_loader()

        self._timer.start()

    def _do_query(self) -> None:
        """Filter the cached address book for the pending prefix."""
        prefix = self._pending_prefix
        if not prefix or len(prefix) < _MIN_CHARS:
            return

        if not self._loaded:
            # Loader still running — show nothing yet
            self._model.setStringList([])
            return

        results = _filter_addresses(self._all_addresses, prefix)
        self._model.setStringList(results)
        if results:
            self.complete()

    # ------------------------------------------------------------------
    # Background loader
    # ------------------------------------------------------------------

    def _start_loader(self) -> None:
        """Launch the background thread to fetch all notmuch addresses."""
        self._loader = _AddressLoader(self)

        def _on_loaded(addresses: list[str]) -> None:
            self._all_addresses = addresses
            self._loaded = True
            self._loader = None
            # Re-trigger query with the pending prefix
            if self._pending_prefix:
                self._do_query()

        self._loader.loaded.connect(_on_loaded)
        self._loader.start()


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


def _filter_addresses(addresses: list[str], prefix: str) -> list[str]:
    """Return entries from *addresses* whose name or email contains
    *prefix* (case-insensitive substring match), limited to 20 results.
    """
    p = prefix.casefold()
    matches: list[str] = []
    for addr in addresses:
        if p in addr.casefold():
            matches.append(addr)
            if len(matches) >= 20:
                break
    return matches
