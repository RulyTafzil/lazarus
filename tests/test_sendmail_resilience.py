"""Sendmail/MIME resilience — msmtp failures, inline images, PGP abort.

These guard against mail loss: a failing msmtp must surface an error
(and never be reported as sent); a deleted inline image must not break
``build_message``; and a PGP failure must abort before the message is
handed to msmtp.
"""
import time

import pytest

from lazarus import compose_threads, mime_builder, pgp_util, settings


class FakePanel:
    """Minimal ComposePanel stand-in (same surface as test_compose_threads)."""

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


@pytest.mark.parametrize('rc,label', [(65, 'data format'), (75, 'temporary failure')])
def test_msmtp_subprocess_failure(tmp_path, qapp, notmuch_stub, rc, label):
    """A non-zero msmtp exit surfaces send_error and is never 'sent'."""
    settings.send_mail_command = f"sh -c 'exit {rc}'"
    settings.sent_dir = str(tmp_path / 'Sent')
    t = _run_send(FakePanel(), qapp)
    assert not t.send_success
    assert str(rc) in t.send_error
    assert 'msmtp' in t.send_error
    assert notmuch_stub.new_calls == 0


def test_send_error_retains_source_context(tmp_path, qapp, notmuch_stub):
    """msmtp stderr is surfaced in the error message for diagnostics."""
    settings.send_mail_command = (
        "sh -c 'echo \"550 data too large\" >&2; exit 65'")
    settings.sent_dir = str(tmp_path / 'Sent')
    t = _run_send(FakePanel(), qapp)
    assert not t.send_success
    assert '550 data too large' in t.send_error


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


def test_pgp_encryption_failure_aborts_send(tmp_path, qapp, notmuch_stub, monkeypatch):
    """A PGP key failure aborts before msmtp is ever invoked."""
    out = tmp_path / 'out.eml'
    settings.send_mail_command = f"sh -c 'cat > {out}'"
    settings.sent_dir = str(tmp_path / 'Sent')

    def boom(eml):
        raise pgp_util.GpgError('no such key')

    monkeypatch.setattr(compose_threads.pgp_util, 'encrypt', boom)

    panel = FakePanel()
    panel.pgp_encrypt = True
    t = _run_send(panel, qapp)

    assert not t.send_success
    assert 'GPG error' in t.send_error
    assert not out.exists()          # msmtp never ran
    assert notmuch_stub.new_calls == 0
