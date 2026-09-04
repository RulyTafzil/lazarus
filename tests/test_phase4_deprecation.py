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
"""Tests for Phase 4 deprecation of lazarus-web / lazarus-server."""

from __future__ import annotations

from unittest.mock import patch
import pytest

from lazarus.server.main import main as server_main


def test_lazarus_web_deprecation_warning():
    """Verify lazarus-web entry point issues DeprecationWarning and delegates to ned."""
    with pytest.deprecated_call(match="lazarus-web"):
        with patch("lazarus.server.main.ned_main", return_value=0) as mock_ned:
            rc = server_main()
            assert rc == 0
            mock_ned.assert_called_once()
