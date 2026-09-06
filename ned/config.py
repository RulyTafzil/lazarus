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

from datetime import datetime
import os
import re
import shutil
import traceback
from typing import Any

from . import notmuch
from . import settings
from .tailscale import detect_tailscale, get_recommended_client_url


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
            "email_address is required. Set settings.email_address in "
            f"{config_path()}"
        )
    elif isinstance(settings.email_address, dict):
        if not settings.email_address:
            errors.append("email_address dict is empty. Add at least one account.")
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
        errors.append("sent_dir is required. Set settings.sent_dir in " + config_path())
    elif isinstance(settings.sent_dir, dict):
        for acct, path in settings.sent_dir.items():
            if path is not None and not isinstance(path, str):
                errors.append(f"sent_dir[{acct!r}] must be a string or None, got {type(path).__name__}")

    # -- smtp_accounts sanity ---------------------------------------------
    if not isinstance(settings.smtp_accounts, list):
        errors.append(f"smtp_accounts must be a list, got {type(settings.smtp_accounts).__name__}")
    elif not settings.smtp_accounts:
        errors.append("smtp_accounts is empty. Add at least ['default']")
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
        errors.append(f"log_level = {settings.log_level!r}. Must be DEBUG/INFO/WARNING/ERROR/CRITICAL")

    return errors


def load_config() -> str:
    """Locate and exec ``~/.config/ned/config.py``, then validate settings.

    :returns: the config file path.
    :raises ConfigError: on missing file, exec failure, or validation errors.
    """
    path = config_path()
    if not os.path.isfile(path):
        raise ConfigError(
            f"No NED config found at {path}.\n"
            "NED reads only ~/.config/ned/config.py. Run `ned --init-config` "
            "to generate it from Notmuch and your local maildir."
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


def _get_notmuch_config(key: str) -> str:
    """Query a value from notmuch config, returning empty string on failure."""
    try:
        r = notmuch.run('config', 'get', key)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ''


def _get_msmtp_accounts() -> dict[str, str]:
    """Parse msmtp config files for email to account name mappings."""
    mappings: dict[str, str] = {}
    for p in ('~/.config/msmtp/config', '~/.msmtprc'):
        path = os.path.expanduser(p)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                current_acct = None
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        cmd, arg = parts[0].lower(), parts[1].strip()
                        if cmd == 'account':
                            current_acct = arg.split(':')[0].strip()
                        elif cmd == 'from' and current_acct and current_acct != 'default':
                            mappings[arg.lower()] = current_acct
        except Exception:
            pass
        if mappings:
            break
    return mappings


def _path_to_display(path: str) -> str:
    """Format a path using ~ if it resides inside user home directory."""
    home = os.path.expanduser('~')
    if path == home:
        return '~'
    if path.startswith(home + os.sep):
        return '~' + path[len(home):]
    return path


def _inspect_notmuch_and_maildir() -> dict[str, Any]:
    """Inspect Notmuch database and local Maildir tree to derive settings."""
    mail_root_raw = _get_notmuch_config('database.mail_root')
    if not mail_root_raw or not os.path.isdir(os.path.expanduser(mail_root_raw)):
        db_path = _get_notmuch_config('database.path')
        if db_path and os.path.isdir(os.path.expanduser(db_path)):
            mail_root_raw = db_path
    if not mail_root_raw or not os.path.isdir(os.path.expanduser(mail_root_raw)):
        default_root = os.path.expanduser('~/Mail')
        if os.path.isdir(default_root):
            mail_root_raw = default_root
        else:
            mail_root_raw = mail_root_raw or '~/Mail'

    expanded_root = os.path.expanduser(mail_root_raw)
    mail_root_disp = _path_to_display(expanded_root)

    user_name = _get_notmuch_config('user.name') or 'User'
    primary_email = _get_notmuch_config('user.primary_email')
    other_raw = _get_notmuch_config('user.other_email')
    other_emails = [e.strip() for e in re.split(r'[;\n]', other_raw) if e.strip()]

    all_emails: list[str] = []
    if primary_email:
        all_emails.append(primary_email)
    for e in other_emails:
        if e and e.lower() not in [x.lower() for x in all_emails]:
            all_emails.append(e)

    dir_entries: list[str] = []
    if os.path.isdir(expanded_root):
        try:
            dir_entries = [
                e for e in os.listdir(expanded_root)
                if not e.startswith('.') and os.path.isdir(os.path.join(expanded_root, e))
            ]
        except OSError:
            pass

    if not all_emails:
        for entry in dir_entries:
            if entry.lower() == 'archive':
                continue
            if '@' in entry:
                all_emails.append(entry)

    msmtp_accounts = _get_msmtp_accounts()

    smtp_accounts: list[str] = []
    email_address_map: dict[str, str] = {}
    sent_dir_map: dict[str, str | None] = {}
    used_names: set[str] = set()

    for email in all_emails:
        local_part, _, domain = email.partition('@')

        matched_dir: str | None = None
        for entry in dir_entries:
            if entry.lower() == email.lower() or entry.lower() == local_part.lower():
                matched_dir = entry
                break
            if domain.lower() in ('gmail.com', 'googlemail.com') and 'gmail' in entry.lower():
                matched_dir = entry
                break

        acct_name = msmtp_accounts.get(email.lower())
        if not acct_name:
            if domain.lower() in ('gmail.com', 'googlemail.com'):
                acct_name = 'gmail'
            elif local_part:
                acct_name = local_part.lower()
            else:
                acct_name = 'default'

        base_name = acct_name
        counter = 1
        while acct_name in used_names:
            counter += 1
            acct_name = f"{base_name}_{counter}"
        used_names.add(acct_name)
        smtp_accounts.append(acct_name)

        if matched_dir and '@' in matched_dir and matched_dir.lower() == email.lower():
            display_email = matched_dir
        else:
            display_email = email
        email_address_map[acct_name] = f"{user_name} <{display_email}>"

        acct_path = os.path.join(expanded_root, matched_dir or acct_name)
        is_gmail = (
            domain.lower() in ('gmail.com', 'googlemail.com')
            or 'gmail' in acct_name.lower()
            or (matched_dir and 'gmail' in matched_dir.lower())
            or (os.path.isdir(acct_path) and os.path.isdir(os.path.join(acct_path, '[Gmail]')))
        )

        if is_gmail:
            sent_dir_map[acct_name] = None
        else:
            sent_cand = None
            if os.path.isdir(acct_path):
                for cand in ('Sent', 'Sent Items', 'sent', 'INBOX.Sent'):
                    if os.path.isdir(os.path.join(acct_path, cand)):
                        sent_cand = cand
                        break
            folder_name = matched_dir or acct_name
            target_sent = sent_cand or 'Sent'
            sent_path = os.path.join(expanded_root, folder_name, target_sent)
            sent_dir_map[acct_name] = _path_to_display(sent_path)

    if not smtp_accounts:
        smtp_accounts = ['default']
        email_address_map['default'] = f"{user_name} <user@example.com>"
        sent_dir_map['default'] = _path_to_display(os.path.join(expanded_root, 'default', 'Sent'))

    archive_path = os.path.join(expanded_root, 'Archive')
    archive_disp = _path_to_display(archive_path)

    return {
        'mail_root': mail_root_disp,
        'smtp_accounts': smtp_accounts,
        'email_address': email_address_map,
        'sent_dir': sent_dir_map,
        'archive_dir': archive_disp,
    }


def init_config() -> tuple[str, str | None]:
    """Create or re-initialize ~/.config/ned/config.py from Notmuch and local maildir.

    If a configuration file already exists, it is backed up with a date
    and timestamp (config.py.YYYYMMDD_HHMMSS.bak) before writing the new one.
    Returns (path_written, backup_path_or_none).
    """
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    backup_path: str | None = None
    if os.path.isfile(path):
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = f"{path}.{ts}.bak"
        shutil.copy2(path, backup_path)

    derived = _inspect_notmuch_and_maildir()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    backup_note = f"# Previous config backed up to: {os.path.basename(backup_path)}\n" if backup_path else ""

    email_lines = [f"    {acct!r}: {addr!r}," for acct, addr in derived['email_address'].items()]
    email_address_repr = "{\n" + "\n".join(email_lines) + "\n}"

    sent_lines = []
    for acct, s_path in derived['sent_dir'].items():
        if s_path is None:
            sent_lines.append(f"    {acct!r}: None,  # Remote server preserves sent mail automatically")
        else:
            sent_lines.append(f"    {acct!r}: {s_path!r},")
    sent_dir_repr = "{\n" + "\n".join(sent_lines) + "\n}"

    ts_info = detect_tailscale()
    if ts_info["serve_url"]:
        detected_web_host = "127.0.0.1"
        client_url = ts_info["serve_url"]
        web_comment = (
            f"# Tailscale Serve reverse proxy is active: {ts_info['serve_url']}\n"
            "# Incoming HTTPS traffic is forwarded to 127.0.0.1:8080.\n"
            "# To connect remote Lazarus desktop clients, run on the client:\n"
            f'# export NED_URL="{client_url}"\n'
        )
    elif ts_info["ts_ip"]:
        detected_web_host = ts_info["ts_ip"]
        client_url = f"http://{ts_info['ts_ip']}:8080"
        dns_note = f", MagicDNS: {ts_info['ts_dns']}" if ts_info["ts_dns"] else ""
        web_comment = (
            f"# Detected Tailscale IP: {ts_info['ts_ip']}{dns_note}\n"
            "# To connect remote Lazarus desktop clients, run on the client:\n"
            f'# export NED_URL="{client_url}"\n'
        )
    else:
        detected_web_host = "127.0.0.1"
        client_url = "http://127.0.0.1:8080"
        web_comment = (
            "# Remote API listener for remote Lazarus desktop clients or web interface:\n"
        )

    content = f"""# =============================================================================
# NED Configuration
#
# Generated by `ned --init-config` on {now_str}.
# Derived from Notmuch database and local Maildir inspection.
{backup_note}#
# NED reads only this file: ~/.config/ned/config.py
# All settings below are standard Python assignments that you can customize.
# =============================================================================

import ned
from ned.rules import Rule


# =============================================================================
# SECTION 1: DERIVED MAIL AND ACCOUNT SETTINGS
# =============================================================================
# The settings below were detected from your Notmuch database and Maildir.
#
# USER EDITABLE:
# You can edit any of these values. For example:
# - Edit contact names in email_address, changing 'User Name <email>' to your name.
# - Adjust sent_dir paths or set an account to None if the server auto-saves.
# - Rename or reorder smtp_accounts to match your msmtp configuration.
# - Change mail_root or archive_dir if your storage layout changes.

# Root directory of your local mail store:
ned.settings.mail_root = {derived['mail_root']!r}

# Account names recognized by send_mail_command and sync_mail_command:
ned.settings.smtp_accounts = {derived['smtp_accounts']!r}

# Outgoing From identity per account.
# Format: 'Display Name <address@domain.com>'
# You can customize the Display Name here for any account:
ned.settings.email_address = {email_address_repr}

# Where sent messages are saved locally per account.
# Set an account to None if your remote server saves sent copies automatically:
ned.settings.sent_dir = {sent_dir_repr}

# Local archive directory for moving mail out of IMAP sync while keeping it indexed:
ned.settings.archive_dir = {derived['archive_dir']!r}

# Command used to dispatch outgoing mail:
ned.settings.send_mail_command = 'msmtp -a "{{account}}" -t'

# Command used to sync mail:
ned.settings.sync_mail_command = 'mbsync -a -V'


# =============================================================================
# SECTION 2: DAEMON AND NETWORK SETTINGS
# =============================================================================
# User configurable daemon runtime settings.

# Logging level: 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
ned.settings.log_level = 'INFO'
ned.settings.log_file = '~/.local/share/ned/ned.log'

# Periodic background mail sync interval in seconds. Set to -1 to disable auto sync:
ned.settings.sync_mail_interval = 300

{web_comment}ned.settings.web_host = {detected_web_host!r}
ned.settings.web_port = 8080

# Bearer token for web API authentication.
# Leave empty string if bound strictly to localhost or Tailscale:
ned.settings.web_token = ''


# =============================================================================
# SECTION 3: FILTER RULES (USER INPUT)
# =============================================================================
# Define custom filter rules applied automatically after each mail sync.
# Rules run against incoming messages matching 'tag:inbox and tag:unread'.
#
# Example rule:
# ned.settings.filter_rules = [
#     Rule(
#         name='Notifications',
#         query='from:noreply@example.com',
#         tag_add=['notifications'],
#         tag_remove=['inbox', 'unread'],
#         move_to={derived['archive_dir']!r},
#     ),
# ]

ned.settings.filter_rules = []
"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path, backup_path