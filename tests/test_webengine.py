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
