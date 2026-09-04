"""compose_threads — SendmailThread with the daemon-routed send path.

The client stub records the finished MIME bytes handed to NED (the
daemon owns msmtp/sent-copy/indexing); the full MIME assembly path runs
for real. ``pgp_sign``/``pgp_encrypt`` are left off — python-gnupg is
optional (failures are covered in test_sendmail_resilience).
"""
import time

from lazarus import compose_threads, mime_builder


class FakePanel:
    """Minimal stand-in for ComposePanel: SendmailThread only touches
    ``_data``, ``account_name()``, ``msg``, ``pgp_sign``/``pgp_encrypt``
    and ``gnupg_keyid()``."""

    mode = ''
    msg = None
    pgp_sign = False
    pgp_encrypt = False

    def __init__(self, **data_kwargs):
        self._data = mime_builder.ComposeData(
            from_addr='Me <me@example.com>',
            to=['bob@example.com'],
            subject='hello',
            body_text='body text',
            **data_kwargs,
        )

    def account_name(self):
        return 'default'

    def gnupg_keyid(self):
        return None


def _run_send(panel, qapp, timeout=10.0):
    t = compose_threads.SendmailThread(panel)
    t.start()
    deadline = time.time() + timeout
    while time.time() < deadline and t.isRunning():
        qapp.processEvents()
        time.sleep(0.01)
    assert not t.isRunning(), 'send thread did not finish in time'
    return t


def test_send_dispatches_finished_mime_to_daemon(qapp, client_stub):
    """The full MIME message (headers + body) reaches NED as raw bytes."""
    t = _run_send(FakePanel(), qapp)

    assert t.send_success
    assert t.send_error == ''
    assert len(client_stub.send_message_calls) == 1
    _acct, payload = client_stub.send_message_calls[0]
    content = payload.decode('utf-8', errors='replace')
    assert 'Subject: hello' in content
    assert 'To: bob@example.com' in content
    assert 'From: Me <me@example.com>' in content
    assert 'body text' in content


def test_send_failure_sets_error(qapp, client_stub):
    client_stub.send_message = lambda account, data: (False, 'send failed: exit 1')

    t = _run_send(FakePanel(), qapp)

    assert not t.send_success
    assert 'exit 1' in t.send_error


def test_reply_sets_references_and_replied_tag(tmp_path, qapp, client_stub):
    # Original message with a References header, as notmuch stores it.
    orig = tmp_path / 'orig.eml'
    orig.write_text(
        'From: Alice <alice@example.com>\n'
        'References: <r1> <r2>\n'
        'Subject: orig\n\nbody\n')
    panel = FakePanel()
    panel.mode = 'reply'
    panel.msg = {'id': 'msg-9', 'filename': [str(orig)]}

    t = _run_send(panel, qapp)

    assert t.send_success
    _acct, payload = client_stub.send_message_calls[0]
    content = payload.decode('utf-8', errors='replace')
    assert 'In-Reply-To: <msg-9>' in content
    assert 'References: <r1> <r2> <msg-9>' in content
    assert any(
        msg_id == 'msg-9' and add == ['replied']
        for msg_id, add, _rem in client_stub.modify_message_tags_calls)