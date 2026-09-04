"""Compose signatures — insertion, account switching, HTML signatures.

Exercises ComposePanel._insert_signature through the real editor;
signatures + accounts are served by the client stub (the daemon's
API, not local config) and the notmuch layer is stubbed.
"""
import pytest
from PyQt6.QtGui import QTextCursor

from lazarus.compose import ComposePanel
from tests.conftest import make_message

_panels = []


@pytest.fixture(autouse=True)
def _cleanup_panels(qapp):
    """Close + destroy every ComposePanel a test opened (widget GC
    hygiene — see test_editor_toolbar's identical fixture)."""
    yield
    for p in _panels:
        p.close()
        p.deleteLater()
    _panels.clear()
    qapp.processEvents()


def _make_panel(qapp, mode='', msg=None, **kw):
    from unittest.mock import MagicMock
    p = ComposePanel(MagicMock(), mode, msg, **kw)
    p.resize(600, 500)
    p.show()
    qapp.processEvents()
    _panels.append(p)
    return p


def _stub_signatures(monkeypatch, sigs):
    """Serve per-account signatures through the client stub.

    ``sigs`` maps account → ``(plaintext, html)``; accounts are added to
    the stub if they are not already configured.
    """
    from lazarus import client as _client_mod
    stub = _client_mod.get_client()
    stub.signatures_info = {
        'use_signature': True,
        'signatures': {a: (t or '') for a, (t, _h) in sigs.items()},
        'signatures_html': {a: (h or '') for a, (_t, h) in sigs.items()},
    }
    if stub.accounts_info['accounts'] == ['default'] and any(
            a != 'default' for a in sigs):
        accts = list(sigs.keys())
        stub.accounts_info = {
            'accounts': accts,
            'email': {a: 'Me <me@example.com>' for a in accts},
            'gnupg_keyid': {a: None for a in accts},
        }
    return stub


def _reply_msg():
    msg = make_message('m1', 'Subj')
    msg['body'] = [{'content-type': 'text/plain', 'content': 'hello body'}]
    return msg


# -- plain-text signatures -------------------------------------------------

def test_reply_puts_sig_above_quote(qapp, monkeypatch):
    _stub_signatures(monkeypatch, {'default': ('Ruly\n', None)})
    p = _make_panel(qapp, mode='reply', msg=_reply_msg())
    text = p.editor.toPlainText()
    assert 'Ruly' in text
    assert 'On ' in text and '> hello body' in text
    assert text.index('Ruly') < text.index('On ')
    assert text.index('-- ') < text.index('> hello body')


def test_account_switch_replaces_sig_in_place(qapp, monkeypatch):
    _stub_signatures(monkeypatch, {
        'default': ('Ruly\n', None),
        'work': ('Work Sig\n', None),
    })
    p = _make_panel(qapp, mode='reply', msg=_reply_msg())
    p._set_account(1)
    text = p.editor.toPlainText()
    assert 'Work Sig' in text
    assert 'Ruly' not in text
    assert text.index('Work Sig') < text.index('On ')
    # switching back restores the original
    p._set_account(0)
    assert 'Ruly' in p.editor.toPlainText()


def test_edited_sig_is_not_duplicated(qapp, monkeypatch):
    """User edited the sig → switching accounts inserts the new block
    above the quote and leaves the user's remnant alone."""
    _stub_signatures(monkeypatch, {
        'default': ('Ruly\n', None),
        'work': ('Work Sig\n', None),
    })
    p = _make_panel(qapp, mode='reply', msg=_reply_msg())
    ed = p.editor
    cur = ed.textCursor()
    idx = ed.toPlainText().find('Ruly')
    cur.setPosition(idx)
    cur.setPosition(idx + len('Ruly'), QTextCursor.MoveMode.KeepAnchor)
    cur.insertText('Ruly edited')

    p._set_account(1)
    text = p.editor.toPlainText()
    assert 'Work Sig' in text
    assert 'Ruly edited' in text           # user's text is preserved
    assert text.index('Work Sig') < text.index('On ')
    assert text.count('Work Sig') == 1     # exactly one new block


def test_switch_to_no_sig_account_removes_block(qapp, monkeypatch):
    _stub_signatures(monkeypatch, {
        'default': ('Ruly\n', None),
        'work': (None, None),
    })
    p = _make_panel(qapp, mode='reply', msg=_reply_msg())
    p._set_account(1)
    text = p.editor.toPlainText()
    assert 'Ruly' not in text
    assert '-- ' not in text
    assert '> hello body' in text


def test_account_switch_roundtrip_no_newline_growth(qapp, monkeypatch):
    """sig → no-sig → sig must reproduce the original document.

    Regression: the removal left the blank-line separator behind and the
    re-insert landed after it, moving the signature down one line per
    switch cycle."""
    _stub_signatures(monkeypatch, {
        'default': ('Ruly\n', None),
        'work': (None, None),
    })
    p = _make_panel(qapp, mode='reply', msg=_reply_msg())
    original = p.editor.toPlainText()
    for _ in range(3):
        p._set_account(1)  # no signature
        p._set_account(0)  # back
        assert p.editor.toPlainText() == original


def test_blank_compose_gets_sig(qapp, monkeypatch):
    _stub_signatures(monkeypatch, {'default': ('Ruly\n', None)})
    p = _make_panel(qapp)
    assert 'Ruly' in p.editor.toPlainText()


# -- HTML signatures -------------------------------------------------------

def test_html_signature_inserted_in_rich_mode(qapp, monkeypatch):
    _stub_signatures(monkeypatch, {'default': (None, '<b>Ruly</b>')})
    p = _make_panel(qapp, mode='reply', msg=_reply_msg())
    text = p.editor.toPlainText()
    assert 'Ruly' in text
    assert text.index('Ruly') < text.index('On ')
    assert 'Ruly' in p.editor.toHtml()  # formatted, not plain fallback


def test_html_signature_switch_replaces(qapp, monkeypatch):
    _stub_signatures(monkeypatch, {
        'default': (None, '<b>Ruly</b>'),
        'work': (None, '<i>Work</i>'),
    })
    p = _make_panel(qapp, mode='reply', msg=_reply_msg())
    p._set_account(1)
    text = p.editor.toPlainText()
    assert 'Work' in text
    assert 'Ruly' not in text
    assert text.index('Work') < text.index('On ')


def test_plain_mode_uses_plain_signature_file(qapp, monkeypatch):
    """With both files present, plaintext compose inserts the plain
    signature, not the HTML one."""
    _stub_signatures(monkeypatch, {
        'default': ('Plain Ruly\n', '<b>Html Ruly</b>'),
    })
    p = _make_panel(qapp, mode='reply', msg=_reply_msg())
    p.editor.toggle_plain()
    p._insert_signature()
    text = p.editor.toPlainText()
    assert 'Plain Ruly' in text
    assert 'Html Ruly' not in text


# -- robustness ------------------------------------------------------------

def test_reply_without_date_header_does_not_crash(qapp, monkeypatch):
    _stub_signatures(monkeypatch, {'default': ('Ruly\n', None)})
    msg = _reply_msg()
    del msg['headers']['Date']
    p = _make_panel(qapp, mode='reply', msg=msg)
    text = p.editor.toPlainText()
    assert 'wrote:' in text
    assert 'Ruly' in text


def test_reply_without_from_header_does_not_crash(qapp, monkeypatch):
    _stub_signatures(monkeypatch, {'default': ('Ruly\n', None)})
    msg = _reply_msg()
    del msg['headers']['From']
    p = _make_panel(qapp, mode='reply', msg=msg)
    assert 'Ruly' in p.editor.toPlainText()
