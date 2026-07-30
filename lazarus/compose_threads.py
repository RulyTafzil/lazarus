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
"""Background threads for compose operations.

:class:`EditorThread` runs the external editor as an escape hatch.
:class:`SendmailThread` builds and sends the MIME message via msmtp.
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import QThread
import mailbox
import email.parser
import tempfile
import os
import subprocess
from subprocess import PIPE, Popen, TimeoutExpired
import traceback
import logging

from . import settings
from . import notmuch
from . import pgp_util
from . import mime_builder

if TYPE_CHECKING:
    from .compose import ComposePanel

logger = logging.getLogger(__name__)


class EditorThread(QThread):
    """A QThread used for editing mail with the external editor."""

    def __init__(self, raw_message: str, panel: ComposePanel,
                 parent: Optional[QThread] = None):
        super().__init__(parent)
        self.panel = panel
        self.raw_message_string = raw_message
        self.file = ''

    def run(self) -> None:
        fd, file = tempfile.mkstemp('.eml')
        self.file = file
        with os.fdopen(fd, 'w') as f:
            f.write(self.raw_message_string)

        cmd = settings.editor_command.format(file=file)
        subprocess.run(cmd, shell=True)

        with open(file, 'r') as f1:
            self.raw_message_string = f1.read()

        if self.panel.is_open:
            os.remove(file)


class SendmailThread(QThread):
    """A QThread used for sending mail."""

    def __init__(self, panel: ComposePanel,
                 parent: Optional[QThread] = None):
        super().__init__(parent)
        self.panel = panel
        self.send_success = False

    def run(self) -> None:
        try:
            data = self.panel._data
            account = self.panel.account_name()

            # Build message via mime_builder
            eml = mime_builder.build_message(data)

            # In-Reply-To and References
            if self.panel.msg and 'id' in self.panel.msg:
                msg_id = f'<{self.panel.msg["id"]}>'
                eml['In-Reply-To'] = msg_id
                refs = [msg_id]
                if ('filename' in self.panel.msg and
                        len(self.panel.msg['filename']) != 0):
                    try:
                        with open(self.panel.msg['filename'][0]) as f:
                            old_msg = email.parser.Parser().parse(
                                f, headersonly=True)
                            if 'References' in old_msg:
                                refs = (old_msg['References'].split()
                                        + refs)
                    except IOError:
                        logger.debug("Couldn't open message for References")
                eml['References'] = ' '.join(refs)

            # PGP
            if self.panel.pgp_sign:
                eml = pgp_util.sign(eml, self.panel.gnupg_keyid())
            if self.panel.pgp_encrypt:
                eml = pgp_util.encrypt(eml)

            # Send
            if isinstance(settings.send_mail_command, dict):
                cmd = settings.send_mail_command[account]
            else:
                cmd = settings.send_mail_command
            cmd = cmd.replace('{account}', account)
            sendmail = Popen(cmd, stdin=PIPE, encoding='utf8', shell=True)
            if sendmail.stdin:
                sendmail.stdin.write(eml.as_string())
                sendmail.stdin.close()
            sendmail.wait(30)

            if sendmail.returncode == 0:
                # Save to sent folder
                if isinstance(settings.sent_dir, dict):
                    sent_dir = settings.sent_dir[account]
                else:
                    sent_dir = settings.sent_dir
                if sent_dir is not None:
                    m = mailbox.MaildirMessage(eml.as_bytes())
                    m.set_flags('S')
                    mailbox.Maildir(sent_dir).add(m)

                notmuch.new(no_hooks=settings.no_hooks_on_send)

                if ((self.panel.mode == 'reply' or
                     self.panel.mode == 'replyall') and
                        self.panel.msg and 'id' in self.panel.msg):
                    notmuch.tag('+replied', 'id:' + self.panel.msg['id'])
                self.panel.set_status('sent', color='fg_good')
                self.send_success = True
            else:
                self.panel.set_status('error', color='fg_bad')
                self.send_success = False
        except TimeoutExpired:
            self.panel.set_status('timed out', color='fg_bad')
        except pgp_util.GpgError as e:
            self.panel.set_status(f'GPG error: {e}', color='fg_bad')
        except Exception:
            traceback.print_exc()
            self.panel.set_status('exception (see stderr)', color='fg_bad')
