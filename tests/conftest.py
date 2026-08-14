"""Shared fixtures and environment setup for the lazarus test suite.

Environment must be prepared before any Qt import happens, so this
module sets ``QT_QPA_PLATFORM=offscreen`` (and the Chromium flags) at
import time.  Tests never touch the real mail database: lazarus.notmuch
is stubbed per-test via the :func:`notmuch_stub` fixture, and QSettings
is redirected to a temp directory.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from unittest.mock import MagicMock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault(
    'QTWEBENGINE_CHROMIUM_FLAGS', '--no-sandbox --disable-gpu')

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# Must be set before the QApplication is constructed (see app.py).
QApplication.setAttribute(
    Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

import pytest  # noqa: E402
from PyQt6.QtCore import QSettings  # noqa: E402

import lazarus.notmuch as notmuch  # noqa: E402


# ---------------------------------------------------------------------------
# Qt session
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def qapp(tmp_path_factory):
    """A session-scoped offscreen QApplication with isolated QSettings."""
    settings_dir = tmp_path_factory.mktemp('qsettings')
    QSettings.setPath(
        QSettings.Format.NativeFormat,
        QSettings.Scope.UserScope,
        str(settings_dir))
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _restore_settings():
    """Snapshot lazarus.settings before each test and restore after.

    Settings is a module of mutable globals that config.py normally
    overrides; tests mutate it freely (themes, mail_root, addresses...)
    and this fixture guarantees no leakage between tests.
    """
    import lazarus.settings as settings
    saved = dict(vars(settings))
    yield
    for k in list(vars(settings)):
        if k not in saved:
            delattr(settings, k)
    for k, v in saved.items():
        setattr(settings, k, v)


# ---------------------------------------------------------------------------
# notmuch stubbing
# ---------------------------------------------------------------------------

def _filter_threads(threads, query: str):
    """Minimal query filter: honours ``thread:<id>`` and ``tag:`` clauses.

    Good enough for the model tests (search + single-thread refresh).
    """
    out = threads
    for clause in query.lower().split(' and '):
        clause = clause.strip(' ()')
        if clause.startswith('thread:'):
            tid = clause[len('thread:'):]
            out = [t for t in out if t.get('thread') == tid]
        elif clause.startswith('tag:'):
            tag = clause[len('tag:'):]
            out = [t for t in out if tag in t.get('tags', [])]
    return out


class NotmuchStub:
    """In-memory stand-in for lazarus.notmuch; records every call."""

    def __init__(self):
        self.threads: list[dict] = []   # search results
        self.tag_list: list[str] = []   # known tags
        self.files: list[str] = []      # search_files results
        self.search_calls: list[str] = []
        self.tag_calls: list[tuple] = []
        self.count_calls: list[tuple] = []
        self.new_calls = 0

    # -- recording helpers -------------------------------------------------
    def search_json(self, query: str, **kwargs) -> str:
        self.search_calls.append(query)
        return json.dumps(_filter_threads(self.threads, query))

    def tags(self) -> list[str]:
        return list(self.tag_list)

    def count(self, query: str, output: str = 'threads') -> int:
        self.count_calls.append((query, output))
        return len(_filter_threads(self.threads, query))

    def count_batch(self, queries: list[str], output: str = 'threads') -> list[int]:
        self.count_calls.append(('__batch__', output))
        return [len(_filter_threads(self.threads, q)) for q in queries]

    def search_files(self, query: str, exclude_false: bool = False) -> list[str]:
        self.search_calls.append('files:' + query)
        return list(self.files)

    def tag(self, tag_expr: str, query: str,
            exclude_marked: bool = False) -> object:
        self.tag_calls.append((tag_expr, query, exclude_marked))
        return MagicMock(returncode=0)

    def new(self, no_hooks: bool = True) -> None:
        self.new_calls += 1


@pytest.fixture
def notmuch_stub(monkeypatch):
    """Patch lazarus.notmuch with an in-memory stub; returns the stub."""
    stub = NotmuchStub()
    for name in ('search_json', 'tags', 'count', 'count_batch',
                 'search_files', 'tag', 'new'):
        monkeypatch.setattr(notmuch, name, getattr(stub, name))
    return stub


def make_thread(thread_id: str, subject: str, tags=None, authors='Alice',
                total: int = 1, timestamp: int = 1700000000,
                date_relative: str = '1 day ago') -> dict:
    """A thread dict shaped like a notmuch search --format=json entry."""
    return {
        'thread': thread_id,
        'timestamp': timestamp,
        'authors': authors,
        'subject': subject,
        'total': total,
        'date_relative': date_relative,
        'matched': total,
        'tags': tags or ['inbox', 'unread'],
    }


def make_message(msg_id: str, subject: str, tags=None, headers=None,
                 timestamp: int = 1700000000) -> dict:
    """A message dict shaped like a notmuch show --format=json entry."""
    return {
        'id': msg_id,
        'timestamp': timestamp,
        'headers': headers or {
            'Subject': subject, 'From': 'Alice <alice@example.com>',
            'To': 'Bob <bob@example.com>', 'Date': 'Thu, 01 Jan 1970 00:00:00 +0000',
        },
        'body': [],
        'tags': tags or ['inbox', 'unread'],
        'crypto': {},
        'match': True,
        'filename': [f'/tmp/{msg_id}'],
        'content-type': 'text/plain',
    }


# ---------------------------------------------------------------------------
# Maildir helpers
# ---------------------------------------------------------------------------

def build_maildir(root, account='default', folders=('INBOX', 'Trash'),
                  n_per_folder: int = 0, uid: int = 1000):
    """Create a minimal Maildir tree: root/account/folder/{cur,new}.

    Returns the root path.
    """
    root = os.path.abspath(root)
    for folder in folders:
        cur = os.path.join(root, account, folder, 'cur')
        new = os.path.join(root, account, folder, 'new')
        os.makedirs(cur, exist_ok=True)
        os.makedirs(new, exist_ok=True)
        for i in range(n_per_folder):
            path = os.path.join(cur, f'msg-{i}:2,S')
            with open(path, 'w') as f:
                f.write(f'From: alice@example.com\nSubject: msg {i}\n\nbody\n')
    return root


@pytest.fixture
def maildir(tmp_path):
    """A fresh Maildir under settings.mail_root, with cleanup."""
    import lazarus.settings as settings
    old_root = settings.mail_root
    old_archive = settings.archive_dir
    root = build_maildir(str(tmp_path / 'Mail'))
    settings.mail_root = str(tmp_path / 'Mail')
    settings.archive_dir = str(tmp_path / 'Mail' / 'Archive')
    os.makedirs(settings.archive_dir, exist_ok=True)
    yield root
    settings.mail_root = old_root
    settings.archive_dir = old_archive


# ---------------------------------------------------------------------------
# App fakes
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_app():
    """A MagicMock stand-in for the PanelApp surface."""
    app = MagicMock()
    app.panel_history = []
    app.sync_thread = None
    app.sync_timer = None
    return app
