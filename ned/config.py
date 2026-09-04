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
"""Config loading and validation for the Notmuch Email Daemon (NED).

NED reads **only** ``~/.config/ned/config.py`` (via ``$XDG_CONFIG_HOME``).
There is deliberately no fallback to the desktop's ``~/.config/lazarus`` —
NED is a standalone daemon and any client is free to configure it
independently. The desktop (``lazarus.config``) reads its own file.

The config file mutates :mod:`ned.settings` via ``ned.settings.X = ...``.
The same friendly ``exec`` model as the desktop is used, with validation
for the mail-routing settings NED cares about.
"""

from __future__ import annotations

import os
import re
import traceback

from . import settings


class ConfigError(RuntimeError):
    """Config file error with a user-friendly message."""


def config_dir() -> str:
    """Return the NED config directory (~/.config/ned by default)."""
    base = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
    return os.path.join(base, 'ned')


def config_path() -> str:
    """Absolute path to the NED config file."""
    return os.path.join(config_dir(), 'config.py')


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
            "email_address is required — set settings.email_address in "
            f"{config_path()}"
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
        errors.append("sent_dir is required — set settings.sent_dir in " + config_path())
    elif isinstance(settings.sent_dir, dict):
        for acct, path in settings.sent_dir.items():
            if path is not None and not isinstance(path, str):
                errors.append(f"sent_dir[{acct!r}] must be a string or None, got {type(path).__name__}")

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

    # -- filter rules ------------------------------------------------------
    if not isinstance(settings.filter_rules, list):
        errors.append(f"filter_rules must be a list, got {type(settings.filter_rules).__name__}")

    # -- log level ---------------------------------------------------------
    if settings.log_level.upper() not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
        errors.append(f"log_level = {settings.log_level!r} — must be DEBUG/INFO/WARNING/ERROR/CRITICAL")

    return errors


_KNOWN_SETTINGS = frozenset(dir(settings))


def _strip_desktop_only(text: str) -> str:
    """Drop assignments to settings NED doesn't own.

    `ned --init-config` copies the desktop config, which carries UI-only
    settings (themes, fonts, tag icons...) that ``ned.settings`` doesn't
    define. Keep only lines whose left-hand attribute is a known
    ``ned.settings`` name; multiline values (``{`` blocks) are skipped
    until their braces balance.
    """
    out: list[str] = []
    depth = 0
    for line in text.splitlines():
        stripped = line.strip()
        # Skip continued lines of a dropped value (brace tracking).
        if depth > 0:
            depth += stripped.count('{') - stripped.count('}')
            continue
        m = re.match(r'^ned\.settings\.([A-Za-z_][A-Za-z0-9_]*)', stripped)
        if m and m.group(1) not in _KNOWN_SETTINGS:
            # Drop the opener and any following brace-balanced continuation.
            depth = stripped.count('{') - stripped.count('}')
            continue
        out.append(line)
    return '\n'.join(out)


def load_config() -> str:
    """Locate and exec ``~/.config/ned/config.py``, then validate settings.

    :returns: the config file path.
    :raises ConfigError: on missing file, exec failure, or validation errors.
    """
    path = config_path()
    if not os.path.isfile(path):
        raise ConfigError(
            f"No NED config found at {path}.\n"
            "NED reads only ~/.config/ned/config.py — copy your mail/routing "
            "settings from ~/.config/lazarus/config.py (run `ned --init-config` "
            "to generate it) and edit as needed."
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
            f"Config errors in {path}:\n{detail}\nFix them and restart NED."
        )

    return path


def init_config() -> str:
    """Create ~/.config/ned/config.py from an existing desktop config.

    Reads ``~/.config/lazarus/config.py`` (if present), rewrites
    ``lazarus.settings`` references to ``ned.settings``, and writes the
    result to the NED config path. Returns the path written.
    """
    path = config_path()
    if os.path.isfile(path):
        raise ConfigError(f"{path} already exists — refusing to overwrite.")

    lazarus_cfg = os.path.join(
        os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config')),
        'lazarus', 'config.py')
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if os.path.isfile(lazarus_cfg):
        with open(lazarus_cfg, 'r', encoding='utf-8') as f:
            text = f.read()
        text = text.replace('lazarus.settings', 'ned.settings')
        text = text.replace('from lazarus.rules import Rule', 'from ned.rules import Rule')
        text = text.replace('import lazarus', 'import ned')
        text = _strip_desktop_only(text)
        header = (
            "# Generated from ~/.config/lazarus/config.py by `ned --init-config`.\n"
            "# NED is standalone — edit THIS file, not the lazarus one.\n"
        )
        content = header + text
    else:
        content = (
            '# No ~/.config/lazarus/config.py found; a minimal ned config.\n'
            'import ned.settings as settings\n'
            "settings.email_address = ''\n"
            "settings.smtp_accounts = ['default']\n"
            "settings.sent_dir = ''\n"
        )

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path