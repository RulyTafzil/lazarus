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
"""Background send thread.

:class:`SendmailThread` builds the MIME message locally (preserving rich
HTML, inline images, and PGP) and hands the finished bytes to the NED
daemon — ``POST /api/v1/send`` raw mode. The daemon owns msmtp, the sent
copy, and indexing; the desktop never shells out to a send command.
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import QObject, QThread
import email.parser
import traceback
import logging

from . import pgp_util
from . import mime_builder

if TYPE_CHECKING:
    from .compose import ComposePanel

logger = logging.getLogger(__name__)


class SendmailThread(QThread):
    """A QThread used for sending mail."""

    def __init__(self, panel: ComposePanel,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self.panel = panel
        self.send_success = False
        self.send_error: str = ''

    def run(self) -> None:
        try:
            data = self.panel._data
            account = self.panel.account_name()

            # Build message via mime_builder
            eml = mime_builder.build_message(data)

            # In-Reply-To and References
            if self.panel.msg and 'id' in self.panel.msg:
                clean_id = str(self.panel.msg["id"]).strip('<>')
                msg_id = f'<{clean_id}>'
                eml['In-Reply-To'] = msg_id
                resolved_refs: Optional[str] = None

                # Try local file parse first when available
                if ('filename' in self.panel.msg and
                        len(self.panel.msg['filename']) != 0):
                    try:
                        with open(self.panel.msg['filename'][0], 'rb') as f:
                            old_msg = email.parser.BytesParser().parse(
                                f, headersonly=True)
                            if 'References' in old_msg:
                                refs = old_msg['References'].split() + [msg_id]
                                resolved_refs = ' '.join(refs)
                    except OSError:
                        logger.debug("Couldn't open message locally for References")

                # If local file was inaccessible (remote daemon or moved file), resolve via NED API
                if resolved_refs is None:
                    try:
                        from .client import get_client
                        seed = get_client().get_reply_seed(clean_id)
                        if seed and seed.get('references'):
                            resolved_refs = str(seed['references'])
                    except Exception as exc:
                        logger.debug("Could not resolve References from NED daemon: %s", exc)

                eml['References'] = resolved_refs or msg_id

            # PGP
            if self.panel.pgp_sign:
                keyid = self.panel.gnupg_keyid()
                if keyid:
                    eml = pgp_util.sign(eml, keyid)
            if self.panel.pgp_encrypt:
                eml = pgp_util.encrypt(eml)

            # Send via NED: the daemon pipes the finished message to
            # msmtp, saves the sent copy, and indexes it. The desktop
            # config carries no mail settings — msmtp/sent_dir live in
            # the daemon's ned config.
            from .client import get_client
            ok, msg_ = get_client().send_message(account, eml.as_bytes())

            if ok:
                if ((self.panel.mode == 'reply' or
                     self.panel.mode == 'replyall') and
                        self.panel.msg and 'id' in self.panel.msg):
                    try:
                        get_client().modify_message_tags(
                            self.panel.msg["id"], add=['replied'])
                    except Exception as e:
                        logger.warning('NED +replied tag failed: %s', e)
                self.send_error = ''
                self.send_success = True
            else:
                self.send_error = msg_ or 'send failed'
                self.send_success = False
        except pgp_util.GpgError as e:
            self.send_error = f'GPG error: {e}'
        except Exception as e:
            traceback.print_exc()
            logger.exception('Unexpected send error')
            self.send_error = f'exception: {e}'