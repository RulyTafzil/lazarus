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
"""Config loading and validation for Lazarus.

``~/.config/lazarus/config.py`` is a Python file that mutates
``lazarus.settings`` via ``lazarus.settings.X = ...``.  We keep that
power-user-friendly ``exec`` model, but wrap it so:

* A broken ``config.py`` surfaces a clear error (file + lineno) instead
  of a cryptic ``AttributeError`` 20 lines later.
* Config mistakes that would silently corrupt mail routing (e.g. a typo
  in ``smtp_accounts``, a mismatched ``email_address`` dict, an empty
  required field) are caught at startup, with a message pointing at the
  setting name.

Callers (``app.Dodo``) should call :func:`load_config` before
re-configuring logging or creating the main window.
"""

from __future__ import annotations

import os
import traceback

from PyQt6.QtCore import QStandardPaths

from . import settings


class ConfigError(RuntimeError):
    """Config file error with a user-friendly message."""


def _config_path() -> tuple[str | None, list[str]]:
    """Return (path or None, searched_locations)."""
    path = QStandardPaths.locate(
        QStandardPaths.StandardLocation.ConfigLocation, 'lazarus/config.py'
    )
    locs = [os.path.join(d, 'lazarus') for d in
            QStandardPaths.standardLocations(QStandardPaths.StandardLocation.ConfigLocation)]
    return (path or None, locs)


def _validate_settings() -> list[str]:
    """Validate settings after config.py has run.

    Returns a list of human-readable error messages (empty means ok).
    Kept deliberately strict for required/mail-routing settings, lenient
    for cosmetic ones.
    """
    errors: list[str] = []

    # -- required fields --------------------------------------------------
    if not settings.email_address:
        errors.append(
            "email_address is required — set settings.email_address in config.py"
        )
    elif isinstance(settings.email_address, dict):
        if not settings.email_address:
            errors.append("email_address dict is empty — add at least one account")
        for acct, addr in settings.email_address.items():
            if not isinstance(addr, str) or not addr.strip():
                errors.append(f"email_address[{acct!r}] is empty or not a string")
            elif '@' not in addr:
                errors.append(f"email_address[{acct!r}] = {addr!r} looks like it is missing '@'")
        # Every account in email_address dict should be in smtp_accounts
        for acct in settings.email_address:
            if acct not in settings.smtp_accounts:
                errors.append(
                    f"email_address has account {acct!r} not in smtp_accounts {settings.smtp_accounts!r}"
                )
    elif isinstance(settings.email_address, str):
        if '@' not in settings.email_address:
            errors.append(f"email_address = {settings.email_address!r} looks like it is missing '@'")

    if not settings.sent_dir and settings.sent_dir is not None:
        # '' means unset; None means intentionally discarded; dict means per-account.
        errors.append("sent_dir is required — set settings.sent_dir in config.py")
    elif isinstance(settings.sent_dir, dict):
        for acct, path in settings.sent_dir.items():
            if path is not None and not isinstance(path, str):
                errors.append(f"sent_dir[{acct!r}] must be a string or None, got {type(path).__name__}")
            elif isinstance(path, str) and path.startswith('~Mail'):
                # Common typo: missing / after ~
                errors.append(
                    f"sent_dir[{acct!r}] = {path!r} starts with '~Mail' — did you mean '~/Mail/...'?"
                )

    # -- smtp_accounts sanity ---------------------------------------------
    if not isinstance(settings.smtp_accounts, list):
        errors.append(f"smtp_accounts must be a list, got {type(settings.smtp_accounts).__name__}")
    elif not settings.smtp_accounts:
        errors.append("smtp_accounts is empty — add at least ['default']")
    else:
        for i, acct in enumerate(settings.smtp_accounts):
            if not isinstance(acct, str) or not acct.strip():
                errors.append(f"smtp_accounts[{i}] must be a non-empty string, got {acct!r}")

    # -- sync interval -----------------------------------------------------
    if not isinstance(settings.sync_mail_interval, int):
        errors.append(f"sync_mail_interval must be int, got {type(settings.sync_mail_interval).__name__}")
    elif settings.sync_mail_interval != -1 and settings.sync_mail_interval < 10:
        errors.append(
            f"sync_mail_interval = {settings.sync_mail_interval} — very short, use -1 to disable or >= 30"
        )

    # -- pane position -----------------------------------------------------
    if settings.thread_pane_position not in ('right', 'left', 'below', 'above'):
        errors.append(
            f"thread_pane_position = {settings.thread_pane_position!r} — must be one of right/left/below/above"
        )

    # -- theme sanity ------------------------------------------------------
    if not isinstance(settings.theme, dict):
        errors.append(f"theme must be a dict (e.g. lazarus.themes.nord), got {type(settings.theme).__name__}")

    # -- filter rules ------------------------------------------------------
    if not isinstance(settings.filter_rules, list):
        errors.append(f"filter_rules must be a list, got {type(settings.filter_rules).__name__}")

    # -- log level ---------------------------------------------------------
    if settings.log_level.upper() not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
        errors.append(f"log_level = {settings.log_level!r} — must be DEBUG/INFO/WARNING/ERROR/CRITICAL")

    return errors


def load_config() -> tuple[str, list[str]]:
    """Locate and exec ``config.py``, then validate settings.

    :returns: (config_path, warnings) — warnings are non-fatal issues
        (empty list means clean).
    :raises ConfigError: on missing file, exec failure, or validation errors.
        The message contains the file path + traceback or the validation errors.
    """
    path, locs = _config_path()
    if not path:
        raise ConfigError(
            "No config.py found in:\n" + "\n".join(f"  {d}/lazarus" for d in locs)
        )

    try:
        code = open(path).read()
        exec(code, {})  # type: ignore[arg-type]
    except SyntaxError as e:
        raise ConfigError(
            f"Syntax error in {path}:{e.lineno}: {e.msg}\n"
            f"  {e.text.strip() if e.text else ''}"
        ) from e
    except Exception as e:
        # Include traceback + file:lineno so a Python error inside config.py is locatable.
        tb = ''.join(traceback.format_exception_only(type(e), e)).strip()
        raise ConfigError(
            f"Error loading {path}:\n{tb}\nCheck the file around the traceback line."
        ) from e

    errors = _validate_settings()
    if errors:
        detail = '\n'.join(f"  - {m}" for m in errors)
        raise ConfigError(
            f"Config errors in {path}:\n{detail}\nFix them and restart Lazarus."
        )

    return path, []
