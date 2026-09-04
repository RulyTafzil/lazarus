"""compose_model — reply/forward/mailto seed builders."""
import lazarus.settings as settings
from lazarus.compose_model import (
    build_mailto_seed, build_reply_seed, build_forward_seed,
    account_for_message, sig_block_text, sig_edit,
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
    seed = build_reply_seed(_msg(), to_all=False)
    assert seed.to_text == 'Alice <alice@example.com>'
    assert seed.subject == 'RE: Original subject'
    assert 'quoted body' in seed.body
    assert 'quoted body' in seed.quoted_tail


def test_reply_seed_skips_self(monkeypatch):
    monkeypatch.setattr('ned.compose_model.settings.email_address',
                        'Bob <bob@example.com>')
    seed = build_reply_seed(_msg(), to_all=False)
    assert 'bob@example.com' not in seed.to_text


def test_reply_all_adds_cc():
    msg = _msg({'Cc': 'Carol <carol@example.com>'})
    seed = build_reply_seed(msg, to_all=True)
    assert 'Carol <carol@example.com>' in seed.cc_text


def test_forward_seed_quotes_original():
    seed = build_forward_seed(_msg())
    assert seed.subject == 'FW: Original subject'
    assert 'quoted body' in seed.body
    assert '---------- Forwarded message' in seed.quoted_tail


def test_reply_seed_two_blank_lines_at_top():
    """Reply bodies start with two blank lines so there's room to type
    above the quoted text (cursor starts at the top)."""
    seed = build_reply_seed(_msg(), to_all=False)
    assert seed.body.startswith('\n\n')
    assert 'quoted body' in seed.body


def test_forward_seed_two_blank_lines_at_top():
    """Forwarded bodies start with two blank lines, same as replies."""
    seed = build_forward_seed(_msg())
    assert seed.body.startswith('\n\n')
    assert 'quoted body' in seed.body
    assert '---------- Forwarded message' in seed.quoted_tail


def test_mailto_seed(tmp_path):
    msg = _msg({'To': 'Bob <bob@example.com>'})
    seed = build_mailto_seed(msg)
    assert seed.to_text


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


def _body_with_sig(sig, quote):
    return 'Hello\n' + sig_block_text(sig) + '\n' + quote


def test_sig_edit_replaces_intact_block():
    quote = 'On Sat, Alice wrote:\n> hi\n'
    body = _body_with_sig('Old', quote)
    start, end, pre, sig, post = sig_edit(body, 'Old', 'New', quote)
    out = body[:start] + pre + sig_block_text(sig) + post + body[end:]
    assert out == _body_with_sig('New', quote)
    assert (pre, sig, post) == ('', 'New', '')


def test_sig_edit_removes_block():
    quote = 'On Sat, Alice wrote:\n> hi\n'
    body = _body_with_sig('Old', quote)
    start, end, pre, sig, post = sig_edit(body, 'Old', '', quote)
    out = body[:start] + pre + sig_block_text(sig) + post + body[end:]
    # the blank-line separator after the block goes with it
    assert out == 'Hello\n' + quote


def test_sig_edit_removal_keeps_separator_when_user_text_follows():
    """The separator is consumed only when it directly follows the
    block — user text after the sig is preserved untouched."""
    body = 'Hello\n' + sig_block_text('Old') + '\nmy notes\n'
    start, end, pre, sig, post = sig_edit(body, 'Old', '', '')
    out = body[:start] + pre + sig_block_text(sig) + post + body[end:]
    assert out == 'Hello\nmy notes\n'


def test_sig_edit_roundtrip_no_newline_growth():
    """sig → no-sig → sig must reproduce the original document."""
    quote = 'On Sat, Alice wrote:\n> hi\n'
    body = _body_with_sig('Ruly', quote)
    start, end, _pre, _sig, _post = sig_edit(body, 'Ruly', '', quote)
    removed = body[:start] + body[end:]
    start2, end2, pre2, sig2, post2 = sig_edit(removed, '', 'Ruly', quote)
    restored = (removed[:start2] + pre2 + sig_block_text(sig2)
                + post2 + removed[end2:])
    assert restored == body


def test_sig_edit_inserts_before_quote_when_old_missing():
    """User edited the old sig away: the new one lands above the quote
    and the user's remnant is left alone."""
    quote = 'On Sat, Alice wrote:\n> hi\n'
    body = 'Hello\n' + quote
    start, end, pre, sig, post = sig_edit(body, 'Old', 'New', quote)
    out = body[:start] + pre + sig_block_text(sig) + post + body[end:]
    assert out == 'Hello\n' + sig_block_text('New') + '\n' + quote


def test_sig_edit_appends_at_end_without_anchor():
    start, end, pre, sig, post = sig_edit('Hello', 'Old', 'New', '')
    assert (start, end) == (len('Hello'), len('Hello'))
    assert pre + sig_block_text(sig) + post == '\n' + sig_block_text('New')


def test_sig_edit_appends_after_trailing_newline():
    """A body that already ends with a newline needs no extra separator."""
    start, end, pre, sig, post = sig_edit('Hello\n', 'Old', 'New', '')
    assert pre == '' and post == '' and sig == 'New'


def test_sig_edit_empty_body():
    start, end, pre, sig, post = sig_edit('', '', 'New', '')
    assert (start, end, pre, post) == (0, 0, '', '')
    assert sig == 'New'  # block alone, no separator


def test_sig_edit_noop():
    assert sig_edit('Hello', '', '', '') == (0, 0, '', '', '')


def test_sig_edit_falls_back_to_append_when_quote_deleted():
    """Anchor gone (user deleted the quote) → append at end instead."""
    body = 'Just some text'
    start, end, pre, sig, post = sig_edit(body, 'Old', 'New', 'On Sat...')
    assert (start, end) == (len(body), len(body))
    assert pre + sig_block_text(sig) + post == '\n' + sig_block_text('New')


def test_subject_with_prefix_no_double():
    from lazarus.compose_model import subject_with_prefix
    assert subject_with_prefix('Subject', 'RE') == 'RE: Subject'
    assert subject_with_prefix('RE: Subject', 'RE') == 'RE: Subject'
    assert subject_with_prefix('re: Subject', 'RE') == 're: Subject'


def test_forward_seed_uses_fetch_part_callback():
    msg = {
        'id': 'msg-forward-test',
        'headers': {'Subject': 'Original email'},
        'body': [
            {'content-type': 'text/plain', 'content': 'Hello there'},
            {
                'id': 2,
                'content-type': 'image/png',
                'filename': 'photo.png',
                'content-disposition': 'attachment',
            },
        ],
    }
    calls = []

    def mock_fetch(mid: str, pid: int) -> bytes:
        calls.append((mid, pid))
        return b'\x89PNGfakeimagebytes'

    seed = build_forward_seed(msg, fetch_part=mock_fetch)
    assert len(calls) == 1
    assert calls[0] == ('msg-forward-test', 2)
    assert len(seed.attachments) == 1
    with open(seed.attachments[0], 'rb') as f:
        assert f.read() == b'\x89PNGfakeimagebytes'

