"""webengine — URL-scheme handler robustness (no QWebEngineView needed).

Deliberately avoids constructing views/pages: those pull in the Chromium
renderer, which is unreliable under pytest (see test_swap_guard history).
A bare QWebEngineUrlSchemeHandler is just a QObject.
"""
from lazarus.webengine import EmbeddedImageHandler


def test_set_message_tolerates_missing_file(qapp):
    """A stale notmuch path (file moved by a background batch) must not
    raise out of refresh_content's call into set_message — the render
    just proceeds without embedded images."""
    h = EmbeddedImageHandler()
    h.set_message('/nonexistent/moved-away.eml')
    assert h.message is None
    assert h.cid_map == {}


def test_set_message_populates_cid_map_from_dict(qapp):
    """When passed a message dict, EmbeddedImageHandler indexes CIDs
    for on-demand retrieval via NedClient."""
    h = EmbeddedImageHandler()
    msg = {
        'id': 'msg-100@example.com',
        'body': [
            {'id': 1, 'content-type': 'text/html', 'content': '<p>Hi</p>'},
            {
                'id': 2,
                'content-type': 'image/png',
                'content-id': '<logo.png@01D7>',
                'filename': 'logo.png',
            },
            {
                'id': 3,
                'content-type': 'image/jpeg',
                'content-id': 'banner.jpg@01D7',
                'filename': 'banner.jpg',
            },
        ],
    }
    h.set_message(msg)
    assert h.message_id == 'msg-100@example.com'
    assert 'logo.png@01D7' in h.cid_map
    assert h.cid_map['logo.png@01D7'] == (2, 'image/png')
    assert 'banner.jpg@01D7' in h.cid_map
    assert h.cid_map['banner.jpg@01D7'] == (3, 'image/jpeg')


def test_set_message_empty_clears_state(qapp):
    h = EmbeddedImageHandler()
    h.set_message({'id': 'x', 'body': [{'id': 1, 'content-id': '<c>', 'content-type': 'image/png'}]})
    assert len(h.cid_map) == 1
    h.set_message(None)
    assert h.cid_map == {}
    assert h.message_id == ''

