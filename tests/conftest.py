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
from unittest.mock import MagicMock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault(
    'QTWEBENGINE_CHROMIUM_FLAGS', '--no-sandbox --disable-gpu')
os.environ.setdefault('LAZARUS_DISABLE_NED', '1')

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# Must be set before the QApplication is constructed (see app.py).
QApplication.setAttribute(
    Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

import pytest  # noqa: E402
from PyQt6.QtCore import QSettings  # noqa: E402

import ned.notmuch as notmuch  # noqa: E402

# Captured before any autouse fixture patches it — real-daemon tests
# (test_desktop_client) re-install this to talk to a live NED.
from lazarus import client as _client_module  # noqa: E402
REAL_GET_CLIENT = _client_module.get_client


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
    """Snapshot lazarus.settings and ned.settings before each test and restore after.

    Settings are modules of mutable globals that config.py normally
    overrides; tests mutate them freely (themes, mail_root, addresses...)
    and this fixture guarantees no leakage between tests. The desktop and
    the daemon own separate settings modules, so both are snapshotted.
    """
    import lazarus.settings as laz_settings
    import ned.settings as ned_settings
    saved_pairs = [(laz_settings, dict(vars(laz_settings))),
                   (ned_settings, dict(vars(ned_settings)))]
    yield
    for mod, saved in saved_pairs:
        for k in list(vars(mod)):
            if k not in saved:
                delattr(mod, k)
        for k, v in saved.items():
            setattr(mod, k, v)


@pytest.fixture(autouse=True)
def _isolate_ned():
    """Ensure tests run with NED disabled by default to avoid touching running daemons."""
    old_disable = os.environ.get("LAZARUS_DISABLE_NED")
    os.environ["LAZARUS_DISABLE_NED"] = "1"
    yield
    if old_disable is not None:
        os.environ["LAZARUS_DISABLE_NED"] = old_disable
    else:
        os.environ.pop("LAZARUS_DISABLE_NED", None)


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


# ---------------------------------------------------------------------------
# NED client stubbing (desktop panels are NED-only)
# ---------------------------------------------------------------------------

class ClientStub:
    """In-memory stand-in for NedClient, returned via lazarus.client.get_client.

    The desktop is a pure NED client, so panel tests exercise the same
    client-call surface the real app uses (`search`, `modify_tags`,
    `trash_thread`, ...) instead of patching `lazarus.notmuch`. Records
    every call like the old NotmuchStub did.
    """

    def __init__(self) -> None:
        self.threads: list[dict] = []        # search results
        self.tag_list: list[str] = []        # known tags
        self.message_ids: list[str] | None = None  # search_messages override
        self.message: dict = {}              # get_thread tree payloads
        self.thread_trees: dict[str, list] = {}  # thread_id -> tree
        self.search_calls: list[str] = []
        self.modify_tags_calls: list[tuple] = []
        self.modify_thread_tags_calls: list[tuple] = []
        self.modify_message_tags_calls: list[tuple] = []
        self.trash_calls: list[str] = []
        self.untrash_calls: list[str] = []
        self.archive_calls: list[str] = []
        self.count_calls: list[tuple] = []
        self.index_new_calls = 0
        self.sync_result: tuple[bool, str] = (True, 'Sync completed (no new mail)')
        # Mail identity served to the compose panel (accounts + signatures
        # come from the daemon, never from local config).
        self.accounts_info: dict = {
            'accounts': ['default'],
            'email': {'default': ''},
            'gnupg_keyid': {'default': None},
        }
        self.signatures_info: dict = {
            'use_signature': True,
            'signatures': {},
            'signatures_html': {},
        }
        self.send_message_calls: list[tuple[str, bytes]] = []

    # -- recording helpers ---------------------------------------------
    def ping(self) -> bool:
        return True

    def search(self, query: str, limit: int = 50, offset: int = 0) -> list[dict]:
        self.search_calls.append(query)
        # Fresh dicts: callers compare by content (e.g. refresh_thread
        # detects a changed row), not by object identity.
        return [dict(t) for t in _filter_threads(self.threads, query)]

    def search_messages(self, query: str, limit: int = 1000, offset: int = 0) -> list[str]:
        self.search_calls.append('messages:' + query)
        if self.message_ids is not None:
            return list(self.message_ids)
        return [str(t['thread']) for t in _filter_threads(self.threads, query)]

    def count(self, query: str, output: str = 'threads') -> int:
        self.count_calls.append((query, output))
        return len(_filter_threads(self.threads, query))

    def count_batch(self, queries: list[str], output: str = 'threads') -> list[int]:
        self.count_calls.append(('__batch__', output))
        return [len(_filter_threads(self.threads, q)) for q in queries]

    def get_tags(self) -> list[dict]:
        return [{'name': t, 'count': len(_filter_threads(self.threads, f'tag:{t}'))}
                for t in self.tag_list]

    def get_contacts(self, query: str = '') -> list[dict]:
        return []

    def get_thread(self, thread_id: str, full: bool = True) -> dict:
        tree = self.thread_trees.get(thread_id, [])
        return {'thread_id': thread_id, 'subject': '', 'tags': [],
                'messages': [], 'tree': tree}

    def get_message(self, msg_id: str) -> dict:
        return dict(self.message) or {'id': msg_id, 'headers': {},
                                      'tags': [], 'body': []}

    def get_part(self, msg_id: str, part_id: int) -> bytes:
        return b'stub-part'

    def modify_tags(
        self,
        queries=(),
        add=None,
        remove=None,
        *,
        threads=None,
        messages=None,
        add_tags=None,
        remove_tags=None,
    ) -> bool:
        q_list = [queries] if isinstance(queries, str) else list(queries or [])
        final_add = list(add or add_tags or [])
        final_remove = list(remove or remove_tags or [])
        self.modify_tags_calls.append((q_list, final_add, final_remove))
        return True

    def modify_thread_tags(self, thread_id: str, add=None, remove=None) -> bool:
        self.modify_thread_tags_calls.append((thread_id, list(add or []), list(remove or [])))
        return True

    def modify_message_tags(self, message_id: str, add=None, remove=None) -> bool:
        self.modify_message_tags_calls.append((message_id, list(add or []), list(remove or [])))
        return True

    def archive_thread(self, thread_or_query) -> bool:
        self.archive_calls.append(thread_or_query if isinstance(thread_or_query, str)
                                  else ','.join(thread_or_query))
        return True

    def trash_thread(self, thread_or_query) -> bool:
        self.trash_calls.append(thread_or_query if isinstance(thread_or_query, str)
                                else ','.join(thread_or_query))
        return True

    def unarchive_thread(self, thread_or_query) -> bool:
        return True

    def untrash_thread(self, thread_or_query) -> bool:
        self.untrash_calls.append(thread_or_query if isinstance(thread_or_query, str)
                                  else ','.join(thread_or_query))
        return True

    def expunge_trash(self) -> int:
        return 0

    def apply_filter_rules(self) -> int:
        return 1

    def index_new(self) -> bool:
        self.index_new_calls += 1
        return True

    def sync_mail(self) -> tuple[bool, str]:
        return self.sync_result

    def get_reply_seed(self, msg_id: str, to_all: bool = False) -> dict:
        return {'to': '', 'cc': '', 'subject': 'RE: ', 'body': ''}

    def get_signatures(self) -> dict:
        return {'signatures': {}}

    def get_accounts_detail(self) -> dict:
        return self.accounts_info

    def get_signatures_detail(self) -> dict:
        return self.signatures_info

    def send_message(self, account: str, message_bytes: bytes) -> tuple[bool, str]:
        self.send_message_calls.append((account, message_bytes))
        return (True, 'Message sent successfully')

    def get_accounts(self) -> list[str]:
        return ['default']

    def send_email(self, *args, **kwargs) -> tuple[bool, str]:
        return (True, 'Message sent successfully')

    def watch_events(self, *args, **kwargs):
        import threading as _t
        return _t.Thread(target=lambda: None, daemon=True)


@pytest.fixture(autouse=True)
def client_stub(monkeypatch):
    """Every desktop test gets a deterministic fake NedClient.

    The desktop is NED-only, so panel/model tests must never touch a live
    daemon (a process-wide ``_TagStore`` loader or a stray panel refresh
    would otherwise read the user's real mailbox). Tests that exercise the
    real daemon (``tests/test_desktop_client.py``) re-install the real
    ``get_client`` inside their own fixtures.
    """
    stub = ClientStub()
    monkeypatch.setattr('lazarus.client.get_client', lambda: stub)
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
    import ned.settings as ned_settings
    # Both processes own settings: the desktop GUI reads lazarus.settings,
    # the daemon's move engines read ned.settings. Keep them pointing at
    # the same tmp Maildir so mixed tests stay consistent.
    pairs = [
        (settings, 'mail_root', 'archive_dir'),
        (ned_settings, 'mail_root', 'archive_dir'),
    ]
    olds = [(m, a, getattr(m, a)) for m, a, _ in pairs]
    root = build_maildir(str(tmp_path / 'Mail'))
    for m, _a1, _a2 in pairs:
        m.mail_root = str(tmp_path / 'Mail')
        m.archive_dir = str(tmp_path / 'Mail' / 'Archive')
        os.makedirs(m.archive_dir, exist_ok=True)
    yield root
    for m, attr, old in olds:
        setattr(m, attr, old)


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
