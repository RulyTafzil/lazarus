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
"""Compatibility shim — :mod:`ned.compose_model` re-exported as ``lazarus.compose_model``.

Qt-free reply/forward seed logic lives with the standalone NED package;
this shim keeps the desktop's public import names and wires the desktop's
settings module into the shared headless helpers (see ``lazarus.util``).
"""

from __future__ import annotations

from . import settings as _laz_settings
import ned.util as _ned_util
import ned.compose_model as _ned_compose_model

# Desktop-process wiring — see lazarus/util.py for the rationale.
_ned_util.settings = _laz_settings
_ned_compose_model.settings = _laz_settings

from ned.compose_model import (  # noqa: F401  (re-exported for desktop modules)
    ComposeSeed,
    account_for_message,
    account_name,
    email_for_account,
    gnupg_keyid_for_account,
    sig_block_text,
    sig_edit,
    quote_body_text,
    forwarded_text,
    normalize_body,
    subject_with_prefix,
    build_mailto_seed,
    build_reply_seed,
    build_forward_seed,
)

__all__ = [
    "ComposeSeed",
    "account_for_message",
    "account_name",
    "email_for_account",
    "gnupg_keyid_for_account",
    "sig_block_text",
    "sig_edit",
    "quote_body_text",
    "forwarded_text",
    "normalize_body",
    "subject_with_prefix",
    "build_mailto_seed",
    "build_reply_seed",
    "build_forward_seed",
]