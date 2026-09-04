#     Lazarus - A fork of Dodo, a graphical, hackable email client based on notmuch
#     Copyright (C) 2021 - Aleks Kissinger
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
"""Headless settings for the Notmuch Email Daemon (NED).

These are the daemon-side defaults, loaded from ``~/.config/ned/config.py``
(see :mod:`ned.config`).  They are deliberately a *separate* module from the
desktop's ``lazarus.settings``: NED is an independent daemon and the desktop
is just one client, so the two read their own configuration files and can
diverge. Only mail-domain settings live here — UI preferences (themes, fonts,
key bindings, tag display) belong to the desktop.

The settings :func:`~ned.settings.email_address` and
:func:`~ned.settings.sent_dir` are required. NED may not work correctly
unless you set them properly. The rest of the settings have reasonable
defaults, as detailed below.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Dict, List, Union

if TYPE_CHECKING:
    from . import rules

# functional
email_address: Union[str, Dict[str, str]] = ''
"""Your email address (REQUIRED)

Used to populate the 'From' field of sent mail and to (mostly)
avoid CC'ing yourself when replying to all. It can be given as
'NAME <ADDRESS@DOMAIN>' format, or as a dictionary mapping the account
names in :func:`~ned.settings.smtp_accounts` to the associated email
addresses.
"""

sent_dir = ''
"""Where to store sent messages (REQUIRED)

This will usually be a subdirectory of the Maildir synced with
:func:`~ned.settings.sync_mail_command`. This setting can be given either
as a string to use one global sent directory, or as a dictionary mapping
account names in :func:`~ned.settings.smtp_accounts` to their own sent dirs.
"""

send_mail_command: str | dict[str, str] = 'msmtp -a "{account}" -t'
"""Command used to send mail via SMTP

Either a plain command or a mapping of account names to command.

The command must be a shell command that expects a (sendmail-compatible)
email message to be written to STDIN. Note that it should read the
destination from the ``From:`` header of the message and not a
command-line argument. Use the ``{account}`` placeholder to read the
currently selected account.
"""

smtp_accounts: List[str] = ['default']
"""A list of SMTP account names recognised by `send_mail_command`

Note this also selects the sync path: with a non-empty list, mail sync runs
``mbsync -V <account>`` per account and :func:`~ned.settings.sync_mail_command`
is ignored (set ``[]`` here to use the shell command instead).
"""

sync_mail_command = 'offlineimap'
"""Shell command used to sync IMAP with local Maildir (fallback path)

Only used when :func:`~ned.settings.smtp_accounts` is empty.  With any
accounts configured, syncing instead runs ``mbsync -V <account>`` for
each account in parallel and this command is ignored.
"""

sync_mail_interval = 300
"""Interval for the daemon's background sync scheduler, in seconds.

Set this to -1 to disable automatic syncing.
"""

wrap_column = 78
"""Wrap quoted replies to this column when building reply seeds.
"""

gnupg_home = None
"""Directory containing GnuPG keys.

If set to None, GnuPG will use whatever directory is the default.
"""

gnupg_keyid = None
"""The id of the key to be used for GnuPG-signing mail messages.

Can be a string, or a dict mapping account names to key ids.
"""

mail_root = '~/Mail'
"""Root directory of the local Maildir.

Used by delete/archive operations to locate per-account Trash folders
and the local Archive.
"""

archive_dir = '~/Mail/Archive'
"""Path to a local-only Maildir where archived messages are moved.

Files are moved into a ``cur/`` subdirectory here, keeping them
searchable in notmuch while removing them from synced IMAP folders.
"""

no_hooks_on_send = True
"""Disable/enable calling notmuch hooks when sending email.

When True, ``notmuch new`` is called with ``--no-hooks`` after a message
is sent. Set to False, for example, when notmuch hooks are used to
archive sent mail.
"""

use_signature = True
"""Whether to automatically attach a per-account signature to replies.

Signatures are loaded from files under the NED config directory
(``~/.config/ned/<account>/signature`` and ``signature.html``) — see
:mod:`ned.signature`.
"""

filter_rules: List[rules.Rule] = []
"""A list of :class:`ned.rules.Rule` mail filters, applied automatically
after every sync (and on demand via ``POST /api/v1/rules``).
"""

filter_scope_query = 'tag:inbox and tag:unread'
"""Notmuch query limiting which mail :func:`~ned.settings.filter_rules`
are allowed to touch.

Rules are applied as ``(filter_scope_query) and (rule.query)``.
"""

# logging
log_level = 'WARNING'
"""Python logging level for NED.

One of ``'DEBUG'``, ``'INFO'``, ``'WARNING'``, ``'ERROR'``, ``'CRITICAL'``.
"""

log_file = ''
"""Path to a log file.  If empty, logs go to stderr only.
"""

# network
web_host: str = '127.0.0.1'
"""Host to bind the optional TCP listener for remote clients."""

web_port: int = 8080
"""Port to bind the optional TCP listener for remote clients."""

web_token: str = ''
"""Bearer token required for the web API. Empty string means no auth
(recommended only if bound strictly to Tailscale/localhost)."""