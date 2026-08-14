"""compose_model — reply/forward/mailto seed builders."""
import lazarus.settings as settings
from lazarus.compose_model import (
    build_mailto_seed, build_reply_seed, build_forward_seed,
    build_blank_seed, account_for_message, sig_block_text,
)
from tests.conftest import make_message


def _msg(extra_headers=None):
    headers = {
        'Subject': 'Original subject',
        'From': 'Alice <alice@example.com>',
        'To': 'Bob <bob@example.com>',
        'Date': 'Thu, 01 Jan 1970 00:00:00 +0000',
    }
    headers.update(extra_headers or {})
    return {
        'id': 'msg-1',
        'timestamp': 1,
        'headers': headers,
        'body': [{'content-type': 'text/plain', 'content': 'quoted body'}],
        'tags': ['inbox'],
        'crypto': {},
    }


def test_reply_seed_addresses_and_subject():
    seed = build_reply_seed(_msg(), None, to_all=False)
    assert seed.to_text == 'Alice <alice@example.com>'
    assert seed.subject == 'RE: Original subject'
    assert 'quoted body' in seed.body


def test_reply_seed_skips_self(monkeypatch):
    monkeypatch.setattr('lazarus.compose_model.settings.email_address',
                        'Bob <bob@example.com>')
    seed = build_reply_seed(_msg(), None, to_all=False)
    assert 'bob@example.com' not in seed.to_text


def test_reply_all_adds_cc():
    msg = _msg({'Cc': 'Carol <carol@example.com>'})
    seed = build_reply_seed(msg, None, to_all=True)
    assert 'Carol <carol@example.com>' in seed.cc_text


def test_forward_seed_quotes_original():
    seed = build_forward_seed(_msg(), None)
    assert seed.subject == 'FW: Original subject'
    assert 'quoted body' in seed.body


def test_mailto_seed(tmp_path):
    msg = _msg({'To': 'Bob <bob@example.com>'})
    seed = build_mailto_seed(msg, None)
    assert seed.to_text


def test_blank_seed_no_crash():
    seed = build_blank_seed(None)
    assert seed.to_text == ''


def test_account_for_message_prefers_from():
    settings.smtp_accounts = ['default', 'work']
    settings.email_address = {
        'default': 'Me <me@default.com>',
        'work': 'Me <me@work.com>',
    }
    # message from our work address -> work account
    msg = _msg({'From': 'Me <me@work.com>'})
    assert account_for_message(msg) == 1
    settings.smtp_accounts = ['default']
    settings.email_address = ''


def test_sig_block_text():
    assert sig_block_text(None) == ''
    assert '-- \n' in sig_block_text('my sig')
    assert sig_block_text('') == ''


def test_subject_with_prefix_no_double():
    from lazarus.compose_model import subject_with_prefix
    assert subject_with_prefix('Subject', 'RE') == 'RE: Subject'
    assert subject_with_prefix('RE: Subject', 'RE') == 'RE: Subject'
    assert subject_with_prefix('re: Subject', 'RE') == 're: Subject'
