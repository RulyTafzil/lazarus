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
"""NED Post-Sync Plugin System.

Provides an asynchronous, non-blocking extension mechanism for executing
post-synchronization tasks such as AI email classification, custom notifications,
or archiving workflows.
"""

from .base import PostSyncContext, PostSyncPlugin
from .runner import PluginRunner, default_runner

__all__ = [
    "PostSyncContext",
    "PostSyncPlugin",
    "PluginRunner",
    "default_runner",
]
