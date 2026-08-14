"""mime_builder — message construction for the three content shapes."""
import email
import os

from lazarus.mime_builder import ComposeData, build_message, _guess_mime


def _data(**overrides) -> ComposeData:
    d = ComposeData()
    d.from_addr = 'Alice <alice@example.com>'
    d.to = ['Bob <bob@example.com>']
    d.subject = 'Hello'
    for k, v in overrides.items():
        setattr(d, k, v)
    return d


def test_plain_text_message():
    msg = build_message(_data(body_text='hello world'))
    assert msg.get('From') == 'Alice <alice@example.com>'
    assert msg.get('To') == 'Bob <bob@example.com>'
    assert msg.get('Subject') == 'Hello'
    assert msg.get_content_type() == 'text/plain'


def test_headers_set():
    msg = build_message(_data(body_text='hi'))
    assert msg.get('User-Agent') == 'Lazarus'
    assert msg.get('Message-ID')            # generated
    assert msg.get('Date')                  # generated


def test_empty_body_coerced():
    msg = build_message(_data())
    assert msg.get_content_type() == 'text/plain'


def test_html_message_is_alternative():
    msg = build_message(_data(body_text='plain', body_html='<p>html</p>'))
    assert msg.get_content_type() == 'multipart/alternative'
    subtypes = sorted(part.get_content_subtype()
                      for part in msg.walk() if part.is_multipart() is False)
    assert 'html' in subtypes and 'plain' in subtypes


def test_inline_image_adds_related_part(tmp_path):
    img = tmp_path / 'pic.png'
    img.write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 64)
    msg = build_message(_data(
        body_text='see image', body_html='<img src="cid:img1">',
        inline_images={'img1': str(img)}))
    assert msg.get_content_type() == 'multipart/alternative'
    # find the image part inside multipart/related
    image_parts = [p for p in msg.walk()
                   if p.get_content_maintype() == 'image']
    assert len(image_parts) == 1
    assert image_parts[0]['Content-ID'] == '<img1>'
    assert image_parts[0].get_payload(decode=True)


def test_attachments_added(tmp_path):
    att = tmp_path / 'doc.txt'
    att.write_text('attachment body')
    msg = build_message(_data(body_text='body', attachments=[str(att)]))
    att_parts = [p for p in msg.walk()
                 if p.get('Content-Disposition', '').startswith('attachment')]
    assert len(att_parts) == 1
    assert att_parts[0].get_filename() == 'doc.txt'
    payload = att_parts[0].get_payload(decode=True)
    assert b'attachment body' in payload


def test_missing_attachment_path_skipped(tmp_path):
    msg = build_message(_data(
        body_text='b', attachments=[str(tmp_path / 'nope.txt')]))
    # message still builds, no attachment part
    disps = [p.get('Content-Disposition', '')
             for p in msg.walk() if p.get('Content-Disposition')]
    assert not any(d.startswith('attachment') for d in disps)


def test_image_attachment_uses_mimeimage(tmp_path):
    img = tmp_path / 'shot.png'
    img.write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 32)
    msg = build_message(_data(
        body_text='b', attachments=[str(img)]))
    parts = [p for p in msg.walk()
             if p.get('Content-Disposition', '').startswith('attachment')]
    assert parts[0].get_content_maintype() == 'image'
    assert parts[0].get_content_subtype() == 'png'


def test_guess_mime():
    assert _guess_mime('x.png') == ('image', 'png')
    assert _guess_mime('x.pdf') == ('application', 'pdf')
    assert _guess_mime('x.unknownext') == ('application', 'octet-stream')


def test_serializable_to_string():
    msg = build_message(_data(body_text='hello'))
    raw = msg.as_string()
    parsed = email.message_from_string(raw)
    assert parsed.get('Subject') == 'Hello'
    assert parsed.get_payload().strip() == 'hello'
