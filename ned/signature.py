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
"""Per-account email signatures for NED.

Signatures are loaded from files under the NED config directory — alongside
``~/.config/ned/config.py`` — not from a Python config to keep them out of
version-controlled dotfiles:

    $XDG_CONFIG_HOME/ned/ACCOUNT/signature       (plain text)
    $XDG_CONFIG_HOME/ned/ACCOUNT/signature.html  (HTML)

($XDG_CONFIG_HOME defaults to ``~/.config`` on Linux.) Either file is
optional. If only ``signature.html`` exists, its plaintext form (via
:func:`ned.util.html2text`) is used as the plaintext signature.

NED is standalone: it reads only the ``ned`` config directory, never the
desktop's ``lazarus`` one. Clients may load signatures themselves (e.g. the
desktop's ``lazarus.signature`` shim re-exports this module, so a local
client still resolves signatures from the NED directory — the daemon is
authoritative for mail identity).
"""

from __future__ import annotations
import os
import logging
from typing import Optional, Tuple

from .config import config_dir
from . import util

logger = logging.getLogger(__name__)


def account_dir(account: str) -> str:
    """Return the NED per-account signature directory."""
    return os.path.join(config_dir(), account)


def _read_file(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except OSError as e:
        logger.warning('Could not read signature file %s: %s', path, e)
        return None


def load(account: str) -> Tuple[Optional[str], Optional[str]]:
    """Load the plaintext and HTML signature for ``account``.

    :param account: an entry from :func:`~ned.settings.smtp_accounts`
    :returns: a ``(text, html)`` pair. Either may be ``None`` if the
        corresponding file is absent. If only the HTML file exists,
        ``text`` is filled in via :func:`ned.util.html2text` so
        plaintext composition still gets a usable signature.
    """
    d = account_dir(account)
    text = _read_file(os.path.join(d, 'signature'))
    html = _read_file(os.path.join(d, 'signature.html'))
    if text is None and html is not None:
        text = util.html2text(html).strip()
    return text, html