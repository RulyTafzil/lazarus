"""address_completer — active-token extraction and popup filtering.

The address book itself is loaded by a background thread (notmuch
address); the loader is exercised with a stubbed notmuch.run.
"""
import json
import time

from PyQt6.QtWidgets import QLineEdit

import lazarus.notmuch as notmuch
from lazarus import address_completer as ac


def test_extract_active_token():
    assert ac._extract_active_token('') == ''
    assert ac._extract_active_token('ali') == 'ali'
    assert ac._extract_active_token('Alice <a@b.c>, Bob') == 'Bob'
    assert ac._extract_active_token('Alice <a@b.c>, Bob >') == 'Bob'


def test_completer_filters_shared_addresses(qapp):
    ac._shared_addresses = ['Alice <alice@example.com>',
                            'Bob <bob@example.com>']
    le = QLineEdit()
    c = ac.AddressCompleter()
    try:
        c.set_line_edit(le)
        le.setText('ali')
        assert c._model.stringList() == ['Alice <alice@example.com>']
        # Completing replaces only the active token, not the whole line.
        c._on_activated('Alice <alice@example.com>')
        assert le.text() == 'Alice <alice@example.com>, '
    finally:
        # Leave no shown widgets behind: a visible completer popup (or a
        # bare QLineEdit) that is later garbage-collected mid-paint can
        # segfault a later test on the shared QApplication.
        ac._shared_addresses = []
        popup = c.popup()
        if popup is not None:
            popup.hide()
        c.deleteLater()
        le.deleteLater()
        qapp.processEvents()


def test_preload_addresses_loads_once(qapp, monkeypatch, notmuch_stub):
    class R:
        returncode = 0
        stdout = json.dumps([{'name-addr': 'Alice <alice@example.com>'}])
    monkeypatch.setattr(notmuch, 'run', lambda *a, **k: R())
    monkeypatch.setattr(ac, '_shared_loader', None)
    monkeypatch.setattr(ac, '_shared_addresses', [])

    ac.preload_addresses()
    ac.preload_addresses()  # second call must be a no-op (singleton)

    deadline = time.time() + 5
    while time.time() < deadline and not ac._shared_addresses:
        qapp.processEvents()
        time.sleep(0.02)
    assert ac._shared_addresses == ['Alice <alice@example.com>']
