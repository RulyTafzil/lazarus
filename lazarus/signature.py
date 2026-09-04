#     Lazarus - A fork of Dodo, a graphical, hackable email client based on notmuch
#     Copyright (C) 2026 - Ruly Tafzil
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
"""Compatibility shim — :mod:`ned.signature` re-exported as ``lazarus.signature``.

Per-account signatures are a NED concern (they live under
``~/.config/ned/<account>/``); this shim keeps the desktop's public
import names so the compose panel resolves signatures from the same
directory the daemon serves.
"""

from __future__ import annotations

from ned.signature import load, account_dir  # noqa: F401  (re-exported for desktop modules)

__all__ = ["load", "account_dir"]