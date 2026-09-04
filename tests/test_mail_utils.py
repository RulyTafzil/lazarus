"""mail_utils + util + html_utils — mail content helpers."""
import pytest

from lazarus import util
from lazarus import mail_utils
from lazarus import html_utils


def _part(content_type, content, filename=None, disposition=None):
    part = {'content-type': content_type, 'content': content}
    if filename:
        part['filename'] = filename
    if disposition:
        part['content-disposition'] = disposition
    return part


def _msg(body_parts):
    return {'id': 'm1', 'body': body_parts, 'headers': {}, 'tags': [], 'crypto': {}}


# -- message_parts / is_attachment -----------------------------------------

def test_message_parts_flat():
    parts = list(mail_utils.message_parts(_msg([
        _part('text/plain', 'hello'),
    ])))
    assert len(parts) == 1
    assert parts[0]['content'] == 'hello'


def test_message_parts_nested():
    parts = list(mail_utils.message_parts(_msg([
        {'content-type': 'multipart/alternative', 'content': [
            _part('text/plain', 'p'),
            _part('text/html', '<b>h</b>'),
        ]},
    ])))
    # the multipart wrapper itself is yielded, then its children
    assert [p['content-type'] for p in parts] == [
        'multipart/alternative', 'text/plain', 'text/html']


def test_is_attachment():
    assert mail_utils.is_attachment(_part('application/pdf', b'x',
                                          filename='a.pdf',
                                          disposition='attachment'))
    assert not mail_utils.is_attachment(_part('text/plain', 'x'))
    assert not mail_utils.is_attachment(_part(
        'application/pgp-signature', '', filename='sig.asc'))


def test_body_text_extracts_plain():
    msg = _msg([_part('text/plain', 'the body')])
    assert mail_utils.body_text(msg) == 'the body'


def test_body_html_extracts_html():
    msg = _msg([_part('text/html', '<p>hi</p>')])
    assert '<p>hi</p>' in mail_utils.body_html(msg)


def test_find_content_by_type():
    msg = _msg([_part('text/html', '<p>a</p>'), _part('text/plain', 'b')])
    assert mail_utils.find_content(msg, 'text/html') == ['<p>a</p>']


def test_sanitize_filename():
    # only '/' is replaced on POSIX
    assert mail_utils.sanitize_filename('a/b.pdf') == 'a_b.pdf'
    assert ':' in mail_utils.sanitize_filename('a:b.pdf')


# -- util ------------------------------------------------------------------

def test_chop_s():
    assert util.chop_s('short') == 'short'
    assert util.chop_s('x' * 30).endswith('...')


def test_separate_headers():
    h, b = util.separate_headers('From: a\nTo: b\n\nbody line 1\nbody line 2\n')
    assert 'From: a' in h
    assert 'body line 1' in b


def test_sort_tags_respects_tag_order(monkeypatch):
    import lazarus.settings as settings
    monkeypatch.setattr(settings, 'tag_order', ['marked', 'Urgent', 'inbox'])
    tags = ['unread', 'inbox', 'Urgent', 'zebra', 'marked']
    assert util.sort_tags(tags) == ['marked', 'Urgent', 'inbox', 'unread', 'zebra']


def test_sort_tags_empty(monkeypatch):
    import lazarus.settings as settings
    monkeypatch.setattr(settings, 'tag_order', [])
    assert util.sort_tags(['b', 'a']) == ['a', 'b']


def test_wrap_message_keeps_quotes():
    text = 'From: a\n\n' + 'x' * 300
    wrapped = util.wrap_message(text)
    # at least one wrapped line shorter than the source
    assert any(len(l) <= 200 for l in wrapped.splitlines())


def test_email_is_me(monkeypatch):
    import lazarus.settings as settings
    # The desktop's utility surface is wired to see lazarus.settings.
    settings.email_address = 'Bob <bob@example.com>'
    assert util.email_is_me('bob@example.com')
    assert not util.email_is_me('alice@example.com')
    settings.email_address = ''


def test_strip_email_address():
    assert util.strip_email_address('Alice <alice@example.com>') == \
        'alice@example.com'


# -- html_utils ------------------------------------------------------------

def test_linkify():
    out = html_utils.linkify('see https://example.com now')
    assert 'href="https://example.com"' in out


def test_simple_escape():
    assert html_utils.simple_escape('<b>&') == '&lt;b&gt;&amp;'


def test_decode_header():
    assert 'subject' in html_utils.decode_header('subject')


def test_colorize_text_returns_text():
    out = html_utils.colorize_text('From: a\n\nbody')
    assert 'body' in out


def test_w3m_html2text(monkeypatch):
    # stub w3m subprocess so the test doesn't depend on it being installed
    class FakeProc:
        stdout = '<b>bold</b>\n'
        returncode = 0

    def fake_run(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr('ned.html_utils.subprocess.run', fake_run)
    out = html_utils.w3m_html2text('<b>bold</b>')
    assert 'bold' in out
