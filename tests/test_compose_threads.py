"""compose_threads — SendmailThread with a stubbed send command.

The send command is pointed at a real ``sh -c 'cat > path'`` so the
full MIME assembly path runs for real (no network, no msmtp); the
notmuch layer is stubbed.  ``pgp_sign``/``pgp_encrypt`` are left off —
python-gnupg is optional.
"""
import time

from lazarus import compose_threads, mime_builder, settings


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


def test_send_writes_message_and_saves_sent(tmp_path, qapp, client_stub):
    out = tmp_path / 'out.eml'
    sent = tmp_path / 'Sent'
    settings.send_mail_command = f"sh -c 'cat > {out}'"
    settings.sent_dir = str(sent)

    t = _run_send(FakePanel(), qapp)

    assert t.send_success
    assert t.send_error == ''
    content = out.read_text()
    assert 'Subject: hello' in content
    assert 'To: bob@example.com' in content
    assert 'From: Me <me@example.com>' in content
    assert 'body text' in content
    # Saved to the sent folder (mailbox.Maildir writes to new/) and
    # re-indexed.
    assert list((sent / 'new').iterdir()) or list((sent / 'cur').iterdir())
    assert client_stub.index_new_calls == 1


def test_send_failure_sets_error(tmp_path, qapp, client_stub):
    settings.send_mail_command = "sh -c 'exit 1'"
    settings.sent_dir = str(tmp_path / 'Sent')

    t = _run_send(FakePanel(), qapp)

    assert not t.send_success
    assert 'exit' in t.send_error
    assert client_stub.index_new_calls == 0


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
    out = tmp_path / 'out.eml'
    settings.send_mail_command = f"sh -c 'cat > {out}'"
    settings.sent_dir = str(tmp_path / 'Sent')

    t = _run_send(panel, qapp)

    assert t.send_success
    content = out.read_text()
    assert 'In-Reply-To: <msg-9>' in content
    assert 'References: <r1> <r2> <msg-9>' in content
    # NED-only: the +replied tag is dispatched to the daemon.
    assert client_stub.modify_tags_calls[-1] == (['id:msg-9'], ['replied'], [])
