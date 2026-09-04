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
"""Per-account email signatures.

Signatures are loaded from files, not from ``config.py`` -- this keeps
them out of version-controlled dotfiles (a signature is personal data,
not configuration) and lets non-Python tooling (a script, a symlink to
a company-wide template, etc.) manage them independently of Lazarus.

For an account named ``ACCOUNT`` (i.e. one of the entries in
:func:`~lazarus.settings.smtp_accounts`), Lazarus looks for:

    $XDG_CONFIG_HOME/lazarus/ACCOUNT/signature       (plain text)
    $XDG_CONFIG_HOME/lazarus/ACCOUNT/signature.html   (HTML)

($XDG_CONFIG_HOME defaults to ~/.config on Linux; resolved via Qt's
QStandardPaths so this also does the right thing on macOS/Windows.)

Either file is optional. If only ``signature.html`` exists, its
plaintext form (via :func:`lazarus.util.html2text`) is used as the
plaintext signature; in rich-text compose mode the HTML content is
inserted directly instead. The HTML content is loaded and returned
regardless, so callers can choose per mode.
"""

from __future__ import annotations
import os
import logging
from typing import Optional, Tuple

from PyQt6.QtCore import QStandardPaths

from . import util

logger = logging.getLogger(__name__)


def config_dir(account: str) -> str:
    """Return $XDG_CONFIG_HOME/{ned,lazarus}/ACCOUNT for the given account name."""
    try:
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.ConfigLocation)
        if not base:
            base = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
    except Exception:
        base = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
    ned_path = os.path.join(base, 'ned', account)
    if os.path.isdir(ned_path):
        return ned_path
    return os.path.join(base, 'lazarus', account)


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

    :param account: an entry from :func:`~lazarus.settings.smtp_accounts`
    :returns: a ``(text, html)`` pair. Either may be ``None`` if the
        corresponding file is absent. If only the HTML file exists,
        ``text`` is filled in via :func:`lazarus.util.html2text` so
        plaintext composition still gets a usable signature.
    """
    candidates = [config_dir(account)]

    try:
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.ConfigLocation)
        if not base:
            base = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
    except Exception:
        base = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))

    # Fallback to alternate config directory if candidate was ned or lazarus
    alt_dirs = [
        os.path.join(base, 'lazarus', account),
        os.path.join(base, 'ned', account),
    ]
    if account in ('default', ''):
        alt_dirs.extend([os.path.join(base, 'ned'), os.path.join(base, 'lazarus')])

    for alt in alt_dirs:
        if alt not in candidates:
            candidates.append(alt)

    for d in candidates:
        text = _read_file(os.path.join(d, 'signature'))
        html = _read_file(os.path.join(d, 'signature.html'))
        if text is not None or html is not None:
            if text is None and html is not None:
                text = util.html2text(html).strip()
            return text, html

    return None, None
