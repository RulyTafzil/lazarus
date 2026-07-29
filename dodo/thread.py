#     Dodo - A graphical, hackable email client based on notmuch
#     Copyright (C) 2021 - Aleks Kissinger
#
# This file is part of Dodo
#
# Dodo is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Dodo is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Dodo. If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations
from typing import List, Optional, Any, Union

from PyQt6.QtCore import *
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import *
from PyQt6.QtWebEngineCore import *
from PyQt6.QtWebEngineWidgets import *

import subprocess
import logging
import email.utils

from . import app
from . import settings
from . import util
from . import keymap
from . import panel
from .webengine import (
    MessagePage,
    MessageHandler,
    EmbeddedImageHandler,
    RemoteBlockingUrlRequestInterceptor,
)
from .thread_model import (
    ThreadModel,
    ThreadItem,
    EmptyThreadError,
    flat_thread,
    short_string,
    iter_thread_messages,
)

logger = logging.getLogger(__name__)


class ThreadPanel(panel.Panel):
    """A panel showing an email thread

    This is the panel used for email viewing.

    :param app: the unique instance of the :class:`~dodo.app.Dodo` app class
    :param thread_id: the unique ID notmuch uses to identify this thread
    """

    def __init__(self, a: app.Dodo, thread_id: str, search_query: str,
                 parent: Optional[QWidget] = None):
        super().__init__(a, parent=parent)
        self.set_keymap(keymap.thread_keymap)
        self.model = ThreadModel(
            thread_id, search_query, settings.default_thread_list_mode)
        self.thread_id = thread_id
        self.query = search_query
        self.html_mode = settings.default_to_html
        self._saved_msg = None
        self._saved_collapsed = None

        self.subject = '(no subject)'

        self.thread_list = QTreeView()
        self.thread_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.thread_list.header().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.thread_list.header().setStretchLastSection(False)
        self.thread_list.setHeaderHidden(True)
        self.thread_list.setRootIsDecorated(False)
        self.thread_list.setModel(self.model)
        self.thread_list.clicked.connect(self._select_index)
        self.model.modelAboutToBeReset.connect(self._prepare_reset)
        self.model.modelReset.connect(self._do_reset)
        self.model.dataChanged.connect(lambda _a, _b: self.refresh_info())
        self.model.messageChanged.connect(
            lambda idx: self.app.update_single_thread(
                self.thread_id,
                msg_id=self.model.message_at(idx)['id'],
            ))

        self.message_info = QTextBrowser()

        # Shared profile for URL scheme handlers
        self.message_profile = QWebEngineProfile(self.app)

        self.image_handler = EmbeddedImageHandler(self)
        self.message_profile.installUrlSchemeHandler(
            b'cid', self.image_handler)

        self.message_handler = MessageHandler(self)
        self.message_profile.installUrlSchemeHandler(
            b'message', self.message_handler)
        self.message_profile.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptEnabled, False)

        self.url_interceptor = RemoteBlockingUrlRequestInterceptor()
        self.message_profile.setUrlRequestInterceptor(self.url_interceptor)
        self.allow_remote_content = False

        # Double-buffered views to prevent white flash during page loads.
        # QStackedLayout in StackAll mode keeps both views compositing
        # continuously — the inactive view is never hidden, just covered,
        # so Chromium keeps it painted and swaps are instant.
        self._views = [self._make_view(), self._make_view()]
        self._active_view = 0
        self._views[1].setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._view_container = QWidget()
        self._view_stack = QStackedLayout()
        self._view_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self._view_stack.addWidget(self._views[0])
        self._view_stack.addWidget(self._views[1])
        self._view_container.setLayout(self._view_stack)

        self.layout_panel()

    # -- view factory -------------------------------------------------------

    def _make_view(self) -> QWebEngineView:
        """Create a configured QWebEngineView with its own MessagePage."""
        view = QWebEngineView()
        page = MessagePage(self.app, self.message_profile, view)
        view.setPage(page)
        view.setZoomFactor(1.2)
        view.setStyleSheet(
            f'background-color: {settings.theme["bg"]};')
        view.page().setBackgroundColor(QColor(settings.theme['bg']))
        return view

    @property
    def message_view(self) -> QWebEngineView:
        """The currently visible view (for backward compat)."""
        return self._views[self._active_view]

    def _inactive_view(self) -> QWebEngineView:
        """The hidden view, ready to load the next page."""
        return self._views[1 - self._active_view]

    # -- collapsed-state save/restore ---------------------------------------

    def _get_collapsed(self) -> set[str]:
        collapsed = set()
        for idx in self.model.iterate_indices():
            if not self.thread_list.isExpanded(idx):
                collapsed.add(self.model.message_at(idx)['id'])
        return collapsed

    def _restore_collapsed(self, collapsed: set[str]) -> None:
        self.thread_list.expandAll()
        for idx in self.model.iterate_indices():
            msg_id = self.model.message_at(idx)['id']
            if msg_id in collapsed:
                self.thread_list.setExpanded(idx, False)

    def _prepare_reset(self) -> None:
        if self.current_index.isValid():
            self._saved_msg = self.current_message['id']
            self._saved_collapsed = self._get_collapsed()

    def _do_reset(self) -> None:
        if self._saved_collapsed is None:
            collapsed = self.model.default_collapsed()
        else:
            collapsed = self._saved_collapsed
            self._saved_collapsed = None
        self._restore_collapsed(collapsed)

        idx = QModelIndex()
        if self._saved_msg:
            idx = self.model.find(self._saved_msg)
            self._saved_msg = None
        if idx.isValid():
            self._select_index(idx)
        else:
            self._select_index(self.model.default_message())

    def toggle_list_mode(self) -> None:
        self.model.toggle_mode()

    # -- selection -----------------------------------------------------------

    def _select_index(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        self.thread_list.setCurrentIndex(index)

        previous_msg = self.message_handler.message_json
        current_msg = self.current_message
        if not previous_msg or current_msg['id'] != previous_msg['id']:
            self.refresh_content()
        # If the tags change the info view will be automatically refreshed.
        if not self.model.mark_as_read(index):
            self.refresh_info()

    def layout_panel(self) -> None:
        """Lay out the thread list, message info, and message view."""
        splitter = QSplitter(Qt.Orientation.Vertical)
        info_area = QSplitter(Qt.Orientation.Horizontal)
        info_area.addWidget(self.thread_list)
        info_area.addWidget(self.message_info)
        splitter.addWidget(info_area)
        splitter.addWidget(self._view_container)
        self.layout().addWidget(splitter)

        # save splitter positions
        window_settings = QSettings("dodo", "dodo")
        main_state = window_settings.value("thread_splitter_state")
        splitter.splitterMoved.connect(
            lambda x: window_settings.setValue(
                "thread_splitter_state", splitter.saveState()))
        if main_state:
            splitter.restoreState(main_state)

        info_area.splitterMoved.connect(
            lambda x: window_settings.setValue(
                "thread_info_state", info_area.saveState()))
        info_state = window_settings.value("thread_info_state")
        if info_state:
            info_area.restoreState(info_state)

    # -- title & refresh -----------------------------------------------------

    def title(self) -> str:
        """The tab title (shortened subject)."""
        return util.chop_s(self.subject)

    def refresh(self) -> None:
        super().refresh()
        try:
            self.model.refresh()
        except EmptyThreadError:
            self.app.close_panel(self)

    def refresh_info(self) -> None:
        """Refresh the header/metadata area without re-fetching content."""
        m = self.current_message

        if 'headers' in m and 'Subject' in m['headers']:
            self.subject = m['headers']['Subject']
        else:
            self.subject = '(no subject)'

        if 'headers' in m:
            header_html = ''
            header_html += (
                f'<table style="background-color: {settings.theme["bg"]}; '
                f'color: {settings.theme["fg"]}; '
                f'font-family: {settings.search_font}; '
                f'font-size: {settings.search_font_size}pt; width:100%">')
            for name in ['Subject', 'Date', 'From', 'To', 'Cc']:
                if name in m['headers']:
                    if name == 'Date':
                        # Convert to local timezone
                        try:
                            dt = email.utils.parsedate_to_datetime(
                                m['headers']['Date'])
                            value = dt.astimezone().strftime('%c')
                        except (ValueError, TypeError):
                            value = m['headers']['Date']
                    else:
                        value = util.simple_escape(m['headers'][name])
                    header_html += (
                        f'<tr>'
                        f'<td><b style="color: {settings.theme["fg_bright"]}">'
                        f'{name}:&nbsp;</b></td>'
                        f'<td>{value}</td>'
                        f'</tr>')
            if 'tags' in m:
                priority = {t: i for i, t in enumerate(settings.tag_order)}
                tags = ' '.join(
                    settings.tag_icons[t] if t in settings.tag_icons
                    else f'[{t}]' for t in sorted(m['tags'], key=lambda t: (priority.get(t, len(settings.tag_order)), t)))
                header_html += (
                    f'<tr>'
                    f'<td><b style="color: {settings.theme["fg_bright"]}">'
                    f'Tags:&nbsp;</b></td>'
                    f'<td><span style="color: {settings.theme["fg_tags"]}; '
                    f'font-family: {settings.tag_font}; '
                    f'font-size: {settings.tag_font_size}">{tags}</span></td>'
                    f'</tr>')
            attachments = [
                f"[{part['filename']}]"
                for part in util.message_parts(m)
                if util.is_attachment(part)
            ]
            if attachments:
                header_html += (
                    f'<tr>'
                    f'<td><b style="color: {settings.theme["fg_bright"]}">'
                    f'Attachments:&nbsp;</b></td>'
                    f'<td><span style="color: {settings.theme["fg_tags"]}">'
                    f'{" ".join(attachments)}</span></td>'
                    f'</tr>')

            # PGP signature status
            if 'signed' in m['crypto']:
                for sig in m['crypto']['signed']['status']:
                    header_html += (
                        f'<tr>'
                        f'<td><b style="color: {settings.theme["fg_bright"]}">'
                        f'Pgp-signed:&nbsp;</b></td>'
                        f'<td>{sig["status"]}: ')
                    if sig['status'] == 'error':
                        header_html += (
                            f'{" ".join(sig["errors"].keys())} '
                            f'(keyid={sig["keyid"]})')
                    elif sig['status'] == 'good':
                        header_html += (
                            f'{sig.get("userid")} ({sig["fingerprint"]})')
                    elif sig['status'] == 'bad':
                        header_html += f'keyid={sig["keyid"]}'
                    header_html += '</td></tr>'

            # Decryption status
            if 'decrypted' in m['crypto']:
                header_html += (
                    f'<tr>'
                    f'<td><b style="color: {settings.theme["fg_bright"]}">'
                    f'Decryption:&nbsp;</b></td>'
                    f'<td>{m["crypto"]["decrypted"]["status"]}</td>'
                    f'</tr>')

            # Message ID
            header_html += '</table>'
            self.message_info.setHtml(header_html)
        self.has_refreshed.emit()

    def refresh_content(self) -> None:
        """Load new content into the hidden view, swap on finish."""
        m = self.current_message
        self.message_handler.message_json = m

        inactive = self._inactive_view()
        inactive.loadFinished.connect(self._on_load_finished)

        if self.html_mode:
            if 'filename' in m and len(m['filename']) != 0:
                self.image_handler.set_message(m['filename'][0])
            inactive.page().setUrl(QUrl('message:html'))
        else:
            inactive.page().setUrl(QUrl('message:plain'))

    def _on_load_finished(self, ok: bool) -> None:
        """Schedule the swap after Chromium has painted a frame.

        loadFinished fires when the DOM is ready, but Chromium hasn't
        composited a frame yet.  Deferring the swap by one event-loop
        cycle lets the renderer paint before we reveal the view.
        """
        view = self.sender()
        if not isinstance(view, QWebEngineView):
            return
        try:
            view.loadFinished.disconnect(self._on_load_finished)
        except TypeError:
            pass
        # Defer swap to let Chromium paint its first frame
        QTimer.singleShot(0, self._do_swap)

    def _do_swap(self) -> None:
        """Raise the freshly loaded view after Chromium has painted."""
        old = self._active_view
        self._active_view = 1 - old
        self._views[old].setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._views[self._active_view].setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._views[self._active_view].raise_()
        self.scroll_message(pos='top')

    def update_thread(self, thread_id: str,
                      msg_id: str | None = None) -> None:
        if self.model.thread_id == thread_id:
            if msg_id and self.model.find(msg_id).isValid():
                try:
                    self.model.refresh_message(msg_id)
                except EmptyThreadError:
                    self.app.close_panel(self)
            else:
                self.dirty = True

    # -- navigation ----------------------------------------------------------

    def next_message(self) -> None:
        self._select_index(self.thread_list.indexBelow(self.current_index))

    def previous_message(self) -> None:
        self._select_index(self.thread_list.indexAbove(self.current_index))

    def next_unread(self) -> None:
        self._select_index(self.model.next_unread(self.current_index))

    def scroll_message(
            self,
            lines: Optional[int] = None,
            pages: Optional[Union[float, int]] = None,
            pos: Optional[str] = None) -> None:
        if pos == 'top':
            self.message_view.page().runJavaScript(
                'window.scrollTo(0, 0)',
                QWebEngineScript.ScriptWorldId.ApplicationWorld)
        elif pos == 'bottom':
            self.message_view.page().runJavaScript(
                'window.scrollTo(0, document.body.scrollHeight)',
                QWebEngineScript.ScriptWorldId.ApplicationWorld)
        elif lines is not None:
            self.message_view.page().runJavaScript(
                f'window.scrollBy(0, {lines} * 20)',
                QWebEngineScript.ScriptWorldId.ApplicationWorld)
        elif pages is not None:
            self.message_view.page().runJavaScript(
                f'window.scrollBy(0, {pages} * 0.9 * window.innerHeight)',
                QWebEngineScript.ScriptWorldId.ApplicationWorld)

    @property
    def current_index(self) -> QModelIndex:
        return self.thread_list.currentIndex()

    @property
    def current_message(self) -> dict:
        return self.model.message_at(self.current_index)

    # -- tagging & actions ---------------------------------------------------

    def toggle_message_tag(self, tag: str) -> None:
        return self.model.toggle_message_tag(self.current_index, tag)

    def tag_message(self, tag_expr: str) -> None:
        return self.model.tag_message(self.current_index, tag_expr)

    def toggle_html(self) -> None:
        """Toggle between HTML and plain text message view."""
        self.html_mode = not self.html_mode
        self.refresh_content()

    def toggle_remote_content(self) -> None:
        """Toggle remote content (images) for the current message view."""
        self.allow_remote_content = not self.allow_remote_content
        self.url_interceptor.allow_remote = self.allow_remote_content
        self.refresh_content()
        self.has_refreshed.emit()

    def reply(self, to_all: bool = True) -> None:
        self.app.open_compose(
            mode='replyall' if to_all else 'reply',
            msg=self.current_message)

    def forward(self) -> None:
        self.app.open_compose(mode='forward', msg=self.current_message)

    def open_attachments(self) -> None:
        """Write attachments to a temp dir and open with the file browser."""
        m = self.current_message
        temp_dir, _ = util.write_attachments(m)
        if temp_dir:
            self.temp_dirs.append(temp_dir)
            cmd = settings.file_browser_command.format(dir=temp_dir)
            subprocess.Popen(cmd, shell=True)
