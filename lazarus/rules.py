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
"""Compatibility shim — :mod:`ned.rules` re-exported as ``lazarus.rules``.

The filter engine moved to the standalone NED package; this one-line
re-export keeps ``from lazarus.rules import Rule`` working in user
``~/.config/lazarus/config.py`` files.
"""

from __future__ import annotations

from ned.rules import Rule, apply_rules  # noqa: F401  (re-exported for config.py users)

__all__ = ["Rule", "apply_rules"]