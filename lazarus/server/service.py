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
"""Backward compatibility alias for lazarus.core.service.

The sys.modules assignment makes ``import lazarus.server.service`` resolve
to the identical module object as ``lazarus.core.service``, so test
monkeypatches on either name affect the same functions. Callers (e.g.
``lazarus.server.app``) should import ``lazarus.core.service`` by its real
name for static-analysis friendliness.
"""

import sys

from ..core import service as _core_service

sys.modules[__name__] = _core_service