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
"""Web-engine helpers for the thread message viewer.

Extracted from ``thread.py`` to keep that module focused on the
``ThreadPanel`` widget.
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import QBuffer, QIODevice, QObject, QUrl, QUrlQuery
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineUrlSchemeHandler, QWebEngineUrlRequestJob,
    QWebEngineUrlRequestInterceptor, QWebEngineNewWindowRequest,
    QWebEngineProfile,
)
from PyQt6.QtGui import QDesktopServices
import email.parser
import re
import subprocess
import sys
import traceback

from . import settings
from . import util
from .protocols import PanelApp

LOCAL_PROTOCOLS = ['cid', 'message']

if TYPE_CHECKING:
    from .app import Dodo
    from .controller import AppController


# ---------------------------------------------------------------------------
# Custom URL-scheme handlers
# ---------------------------------------------------------------------------

class MessageHandler(QWebEngineUrlSchemeHandler):
    """Serve ``message:html`` and ``message:plain`` URLs from the
    currently displayed message JSON."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.message_json: Optional[dict] = None

    def requestStarted(self, request: QWebEngineUrlRequestJob) -> None:
        mode = request.requestUrl().toString()[len('message:'):]

        if self.message_json:
            buf = QBuffer(parent=self)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            if mode == 'html':
                html = util.body_html(self.message_json)
                if html:
                    html = re.sub(
                        r'(<meta(?!\s*(?:name|value)\s*=)[^>]*?charset\s*=[\s"\']*)([^\s"\'/>]*)',
                        r'\1utf-8', html, flags=re.M,
                    )
                    buf.write(html.encode('utf-8'))
                else:
                    mode = 'plain'  # fall through to plaintext rendering
            if mode != 'html':
                for filt in settings.message2html_filters:
                    try:
                        text = filt(self.message_json)
                    except Exception:
                        print(
                            f"Error in message2html filter {filt.__name__}, ignoring:",
                            file=sys.stderr,
                        )
                        traceback.print_exc(file=sys.stderr)
                        continue
                    if text is not None:
                        break
                else:
                    text = util.simple_escape(
                        util.body_text(self.message_json))
                    text = util.colorize_text(text)
                    text = util.linkify(text)

                if text:
                    buf.write(f"""
                    <html>
                    <head>
                    <meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>
                    <style type="text/css">
                    {util.make_message_css()}
                    </style>
                    </head>
                    <body>
                    <pre style="white-space: pre-wrap">{text}</pre>
                    </body>
                    </html>""".encode('utf-8'))

            buf.close()
            request.reply('text/html;charset=utf-8'.encode('latin1'), buf)
        else:
            request.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)


class EmbeddedImageHandler(QWebEngineUrlSchemeHandler):
    """Serve ``cid:`` URLs by reading the raw message file."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.message: Optional[email.message.Message] = None

    def set_message(self, filename: str) -> None:
        with open(filename, 'rb') as f:
            self.message = email.parser.BytesParser().parse(f)

    def requestStarted(self, request: QWebEngineUrlRequestJob) -> None:
        cid = request.requestUrl().toString()[len('cid:'):]
        content_type = None
        if self.message:
            for part in self.message.walk():
                if ("Content-id" in part
                        and part["Content-id"] == f'<{cid}>'):
                    content_type = part.get_content_type()
                    buf = QBuffer(parent=self)
                    buf.open(QIODevice.OpenModeFlag.WriteOnly)
                    buf.write(part.get_payload(decode=True))
                    buf.close()
                    request.reply(content_type.encode('latin1'), buf)
                    break
        if not content_type:
            request.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)


class RemoteBlockingUrlRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Block remote network requests unless explicitly allowed.

    By default, blocking follows :attr:`settings.html_block_remote_requests`.
    Set :attr:`allow_remote` to ``True`` on the interceptor instance to
    override the global setting and allow all remote requests for the
    current thread view.
    """

    def __init__(self) -> None:
        super().__init__()
        self.allow_remote = False

    def interceptRequest(self, info):
        if info.requestUrl().scheme() not in LOCAL_PROTOCOLS:
            blocked = settings.html_block_remote_requests and not self.allow_remote
            info.block(blocked)


# ---------------------------------------------------------------------------
# Custom web page
# ---------------------------------------------------------------------------

class MessagePage(QWebEnginePage):
    """A ``QWebEnginePage`` that handles link clicks and navigation.

    ``mailto:`` links open a compose panel.  Other external links are
    opened in the system browser (with an optional confirmation).
    """

    def __init__(self, a: PanelApp, profile: QWebEngineProfile,
                 parent: Optional[QObject] = None):
        super().__init__(profile, parent)
        self._app = a
        self.newWindowRequested.connect(self._on_new_window_requested)

    def javaScriptConsoleMessage(
            self, level: QWebEnginePage.JavaScriptConsoleMessageLevel,
            message: str, line: int, source: str) -> None:
        """Suppress JS console noise from malformed email HTML."""
        pass

    def _on_new_window_requested(
            self, req: QWebEngineNewWindowRequest) -> None:
        self._handle_link(req.requestedUrl())

    def _handle_link(self, url: QUrl) -> None:
        if url.scheme() == 'mailto':
            query = QUrlQuery(url)
            msg = {
                'headers': {
                    'To': url.path(),
                    'Subject': query.queryItemValue('subject'),
                }
            }
            self._app.open_compose(mode='mailto', msg=msg)
        else:
            if (not settings.html_confirm_open_links
                    or url.host() in settings.html_confirm_open_links_trusted_hosts
                    or QMessageBox.question(
                        None, 'Open link',
                        f'Open the following URL in browser?\n\n  {url.toString()}'
                    ) == QMessageBox.StandardButton.Yes):
                if settings.web_browser_command == '':
                    QDesktopServices.openUrl(url)
                else:
                    subprocess.Popen(
                        [settings.web_browser_command, url.toString()],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )

    def acceptNavigationRequest(
            self, url: QUrl, ty: QWebEnginePage.NavigationType,
            isMainFrame: bool) -> bool:
        if url.scheme() in LOCAL_PROTOCOLS:
            return True
        if ty == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            self._handle_link(url)
            return False
        if ty == QWebEnginePage.NavigationType.NavigationTypeRedirect:
            return False  # never allow <meta> redirects
        return settings.html_block_remote_requests
