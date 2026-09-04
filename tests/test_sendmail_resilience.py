"""Sendmail/MIME resilience — daemon send failures, inline images, PGP abort.

These guard against mail loss: a NED send failure must surface an error
(and never be reported as sent — nor mark the original +replied); a
deleted inline image must not break ``build_message``; and a PGP failure
must abort before the message reaches the daemon.
"""
import time

import pytest

from lazarus import compose_threads, mime_builder, pgp_util


class FakePanel:
    """Minimal ComposePanel stand-in."""

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


def _fail(stub, message='Send command failed: Exit code 65'):
    """Make the client stub's send_message fail with *message*."""
    stub.send_message = lambda account, data: (False, message)


def test_send_failure_surfaces_error(qapp, client_stub):
    """A non-zero msmtp exit (reported by NED) surfaces send_error and is
    never 'sent'."""
    _fail(client_stub, 'Send command failed: Exit code 65')
    t = _run_send(FakePanel(), qapp)
    assert not t.send_success
    assert '65' in t.send_error
    assert 'Send command failed' in t.send_error


def test_send_error_retains_source_context(qapp, client_stub):
    """The daemon's stderr detail is surfaced in the error message."""
    _fail(client_stub, "Send command failed: 550 data too large")
    t = _run_send(FakePanel(), qapp)
    assert not t.send_success
    assert '550 data too large' in t.send_error


def test_send_ok_dispatches_raw_and_marks_replied(qapp, client_stub):
    """On success the finished MIME bytes go to the daemon and the reply
    original is tagged +replied."""
    panel = FakePanel()
    panel.mode = 'reply'
    panel.msg = {'id': 'orig123', 'filename': []}
    t = _run_send(panel, qapp)
    assert t.send_success, t.send_error
    assert len(client_stub.send_message_calls) == 1
    acct, payload = client_stub.send_message_calls[0]
    assert acct == 'default'
    assert b'Subject: hello' in payload
    assert any(
        q == ['id:orig123'] and add == ['replied']
        for q, add, _rem in client_stub.modify_tags_calls)


def test_send_failure_never_marks_replied(qapp, client_stub):
    """A failed send must not tag the original +replied."""
    _fail(client_stub)
    panel = FakePanel()
    panel.mode = 'reply'
    panel.msg = {'id': 'orig123', 'filename': []}
    t = _run_send(panel, qapp)
    assert not t.send_success
    assert not any(
        add == ['replied'] for _q, add, _rem in client_stub.modify_tags_calls)


def test_mime_builder_missing_inline_image(tmp_path):
    """A deleted inline image is skipped, not raising an exception."""
    missing = str(tmp_path / 'gone.png')
    data = mime_builder.ComposeData(
        from_addr='Me <me@example.com>',
        to=['bob@example.com'],
        subject='hi',
        body_text='plain',
        body_html='<img src="cid:pic">',
        inline_images={'pic': missing},
    )
    eml = mime_builder.build_message(data)  # must not raise
    s = eml.as_string()
    # HTML + inline image still yields a related part; the missing image
    # is skipped silently (the CSS src reference remains, by design).
    assert 'multipart/related' in s


def test_mime_builder_missing_attachment(tmp_path):
    """A missing file attachment is skipped without raising."""
    missing = str(tmp_path / 'gone.bin')
    data = mime_builder.ComposeData(
        from_addr='Me <me@example.com>',
        to=['bob@example.com'],
        subject='hi',
        body_text='body',
        attachments=[missing],
    )
    eml = mime_builder.build_message(data)
    assert 'gone.bin' not in eml.as_string()


def test_pgp_encryption_failure_aborts_send(qapp, client_stub, monkeypatch):
    """A PGP key failure aborts before the message reaches the daemon."""
    def boom(eml):
        raise pgp_util.GpgError('no such key')

    monkeypatch.setattr(compose_threads.pgp_util, 'encrypt', boom)

    panel = FakePanel()
    panel.pgp_encrypt = True
    t = _run_send(panel, qapp)

    assert not t.send_success
    assert 'GPG error' in t.send_error
    assert client_stub.send_message_calls == []