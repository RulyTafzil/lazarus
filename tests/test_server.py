"""Tests for the Lazarus mobile web server and REST API."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
import pytest

import lazarus.notmuch as notmuch
import lazarus.settings as settings
from lazarus.server import app, service


@pytest.fixture(autouse=True)
def reset_server_settings():
    old_host = getattr(settings, 'web_host', '127.0.0.1')
    old_port = getattr(settings, 'web_port', 8080)
    old_token = getattr(settings, 'web_token', '')
    old_accounts = list(settings.smtp_accounts)
    settings.web_token = ''
    yield
    settings.web_host = old_host
    settings.web_port = old_port
    settings.web_token = old_token
    settings.smtp_accounts = old_accounts


def test_service_get_contacts(monkeypatch):
    sample_addresses = json.dumps([
        {"name": "Alice Smith", "address": "alice@example.com"},
        {"name": "Bob Jones", "address": "bob@example.com"},
        {"name": "", "address": "charlie@example.com"},
    ])

    def mock_run(*args, **kwargs):
        class Result:
            returncode = 0
            stdout = sample_addresses
            stderr = ''
        return Result()

    monkeypatch.setattr(notmuch, 'run', mock_run)
    service.load_contacts(force_reload=True)

    all_contacts = service.get_contacts()
    assert len(all_contacts) == 3
    assert all_contacts[0]['display'] == 'Alice Smith <alice@example.com>'

    # Substring search
    alice_matches = service.get_contacts('alice')
    assert len(alice_matches) == 1
    assert alice_matches[0]['address'] == 'alice@example.com'

    # Domain search
    domain_matches = service.get_contacts('example.com')
    assert len(domain_matches) == 3


def test_service_search_threads(monkeypatch):
    sample_search = json.dumps([
        {
            "thread": "0000000000001234",
            "timestamp": 1600000000,
            "date_relative": "Today",
            "matched": 1,
            "total": 1,
            "authors": "Alice",
            "subject": "Hello mobile",
            "tags": ["inbox", "unread"]
        }
    ])

    def mock_run(*args, **kwargs):
        class Result:
            returncode = 0
            stdout = sample_search
            stderr = ''
        return Result()

    monkeypatch.setattr(notmuch, 'run', mock_run)
    threads = service.search_threads('tag:inbox')
    assert len(threads) == 1
    assert threads[0]['thread'] == '0000000000001234'
    assert threads[0]['subject'] == 'Hello mobile'


def test_service_tag_actions(monkeypatch):
    recorded_calls: list[tuple[str, str]] = []

    def mock_tag(expr: str, query: str, *args, **kwargs):
        recorded_calls.append((expr, query))
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr(notmuch, 'tag', mock_tag)
    from lazarus import actions
    monkeypatch.setattr(actions, 'collect_files', lambda q: [])

    assert service.archive_thread('0000000000001234')
    assert len(recorded_calls) == 1
    assert recorded_calls[-1] == ('-inbox -unread', 'thread:0000000000001234')

    assert service.trash_thread('0000000000001234')
    assert len(recorded_calls) == 2
    assert recorded_calls[-1] == ('+trash -inbox -unread', 'thread:0000000000001234')

    assert service.unarchive_thread('0000000000001234')
    assert len(recorded_calls) == 3
    assert recorded_calls[-1] == ('+inbox', 'thread:0000000000001234')

    assert service.untrash_thread('0000000000001234')
    assert len(recorded_calls) == 4
    assert recorded_calls[-1] == ('+inbox -trash', 'thread:0000000000001234')

    assert service.toggle_flag('0000000000001234', True)
    assert len(recorded_calls) == 5
    assert recorded_calls[-1] == ('+flagged', 'thread:0000000000001234')


def test_service_archive_moves_to_local_archive(tmp_path, monkeypatch):
    import os
    from lazarus import actions

    archive_dir = str(tmp_path / 'Archive')
    settings.archive_dir = archive_dir

    src_dir = tmp_path / 'Mail' / 'default' / 'Inbox' / 'cur'
    src_dir.mkdir(parents=True)
    mail_file = src_dir / '12345.msg,U=10:2,S'
    mail_file.write_text('From: test@example.com\n\nHello')

    monkeypatch.setattr(notmuch, 'tag', lambda expr, q, *args, **kwargs: None)
    monkeypatch.setattr(notmuch, 'new', lambda no_hooks=True: None)
    monkeypatch.setattr(actions, 'collect_files', lambda q: [str(mail_file)])

    assert service.archive_thread('0000000000001234')
    actions.get_worker().wait_idle()

    # Source file should be moved into archive cur/
    dest_cur = tmp_path / 'Archive' / 'cur'
    assert dest_cur.is_dir()
    archived_files = list(dest_cur.iterdir())
    assert len(archived_files) == 1
    # UID annotation stripped
    assert ',U=10' not in archived_files[0].name


def test_service_sync_mail(monkeypatch):
    called = []
    monkeypatch.setattr(notmuch, 'new', lambda no_hooks=True: called.append('new'))
    settings.smtp_accounts = []
    settings.sync_mail_command = 'echo sync'

    ok, msg = service.sync_mail()
    assert ok
    assert 'Sync completed' in msg
    assert 'new' in called


def test_service_get_thread_messages(monkeypatch):
    sample_show = json.dumps([
        [[
            {
                "id": "msg-123@example.com",
                "match": True,
                "timestamp": 1600000000,
                "date_relative": "Today",
                "tags": ["inbox"],
                "headers": {
                    "Subject": "Discussion topic",
                    "From": "Alice <alice@example.com>",
                    "To": "Bob <bob@example.com>",
                    "Date": "Wed, 02 Sep 2026 12:00:00 +0000"
                },
                "body": [
                    {
                        "id": 1,
                        "content-type": "text/plain",
                        "content": "Hi Bob,\nLet us talk about the project."
                    }
                ]
            },
            []
        ]]
    ])

    def mock_run(*args, **kwargs):
        class Result:
            returncode = 0
            stdout = sample_show
            stderr = ''
        return Result()

    monkeypatch.setattr(notmuch, 'run', mock_run)
    data = service.get_thread_messages('0000000000001234')
    assert data['thread_id'] == '0000000000001234'
    assert data['subject'] == 'Discussion topic'
    assert len(data['messages']) == 1
    assert data['messages'][0]['body_text'] == "Hi Bob,\nLet us talk about the project."


@pytest.fixture
def running_test_server():
    server = app.create_server('127.0.0.1', 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()
    server.server_close()


def test_http_static_files(running_test_server):
    # Index HTML
    req = urllib.request.Request(f"{running_test_server}/")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        html = resp.read().decode('utf-8')
        assert '<title>Lazarus Mail</title>' in html

    # CSS
    req = urllib.request.Request(f"{running_test_server}/static/app.css")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        css = resp.read().decode('utf-8')
        assert '--bg-primary:' in css

    # JS
    req = urllib.request.Request(f"{running_test_server}/static/app.js")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        js = resp.read().decode('utf-8')
        assert 'Lazarus Mobile Web Client' in js


def test_http_api_endpoints(running_test_server, monkeypatch):
    monkeypatch.setattr(service, 'search_threads', lambda q, limit, offset: [
        {'thread': 'test1', 'subject': 'Test Subject'}
    ])
    monkeypatch.setattr(service, 'get_all_tags', lambda: [
        {'name': 'inbox', 'count': 42}
    ])
    settings.smtp_accounts = ['work', 'personal']

    # Search endpoint
    with urllib.request.urlopen(f"{running_test_server}/api/search?q=tag:inbox") as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode('utf-8'))
        assert data[0]['thread'] == 'test1'

    # Accounts endpoint
    with urllib.request.urlopen(f"{running_test_server}/api/accounts") as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode('utf-8'))
        assert data['accounts'] == ['work', 'personal']

    # Tags endpoint
    with urllib.request.urlopen(f"{running_test_server}/api/tags") as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode('utf-8'))
        assert data[0]['name'] == 'inbox'


def test_http_bearer_token_auth(running_test_server):
    settings.web_token = 'topsecret123'

    # Unauthorized without header
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{running_test_server}/api/search")
    assert exc_info.value.code == 401

    # Authorized with header
    req = urllib.request.Request(
        f"{running_test_server}/api/accounts",
        headers={'Authorization': 'Bearer topsecret123'}
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200

    # Authorized with query string
    req2 = urllib.request.Request(f"{running_test_server}/api/accounts?token=topsecret123")
    with urllib.request.urlopen(req2) as resp:
        assert resp.status == 200


def test_http_sync_endpoint(running_test_server, monkeypatch):
    monkeypatch.setattr(service, 'sync_mail', lambda: (True, 'Sync completed successfully'))

    req = urllib.request.Request(f"{running_test_server}/api/sync", data=b'', method='POST')
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode('utf-8'))
        assert data['ok'] is True
        assert 'Sync completed' in data['message']


def test_signatures(running_test_server, monkeypatch):
    from lazarus import signature
    settings.smtp_accounts = ['personal', 'work']
    monkeypatch.setattr(signature, 'load', lambda acct: (f"Sig for {acct}\n", None))

    data = service.get_signatures()
    assert data['use_signature'] is True
    assert data['signatures']['personal'] == 'Sig for personal\n'
    assert data['signatures']['work'] == 'Sig for work\n'

    # HTTP endpoint test
    with urllib.request.urlopen(f"{running_test_server}/api/signatures") as resp:
        assert resp.status == 200
        http_data = json.loads(resp.read().decode('utf-8'))
        assert http_data['use_signature'] is True
        assert http_data['signatures']['personal'] == 'Sig for personal\n'


def test_url_encoded_message_id_reply_seed(running_test_server, monkeypatch):
    recorded_id = []

    def mock_get_reply_seed(mid: str, to_all: bool = False):
        recorded_id.append(mid)
        return {'to': 'alice@example.com', 'subject': 'RE: Test'}

    monkeypatch.setattr(service, 'get_reply_seed', mock_get_reply_seed)

    encoded_mid = urllib.parse.quote("msg-123@domain.com")
    url = f"{running_test_server}/api/messages/{encoded_mid}/reply-seed"
    with urllib.request.urlopen(url) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode('utf-8'))
        assert data['to'] == 'alice@example.com'

    # Verify that unquoted ID was received by service
    assert recorded_id[0] == "msg-123@domain.com"



