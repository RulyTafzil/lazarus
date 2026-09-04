"""address_completer — active-token extraction and popup filtering.

The address book itself is loaded by a background thread (NED
contacts); the loader is exercised with a stubbed client.
"""
import time

from PyQt6.QtWidgets import QLineEdit

from lazarus import address_completer as ac


def test_extract_active_token():
    assert ac._extract_active_token('') == ''
    assert ac._extract_active_token('ali') == 'ali'
    assert ac._extract_active_token('Alice <a@b.c>, Bob') == 'Bob'
    assert ac._extract_active_token('Alice <a@b.c>, Bob >') == 'Bob'


def _type_into(line_edit, text, qapp):
    """Drive *line_edit* with real user keystrokes (fires ``textEdited``)."""
    from PyQt6.QtTest import QTest
    line_edit.setFocus()
    line_edit.clear()
    qapp.processEvents()
    QTest.keyClicks(line_edit, text)
    qapp.processEvents()


def test_completer_filters_shared_addresses(qapp):
    ac._shared_addresses = ['Alice <alice@example.com>',
                            'Bob <bob@example.com>']
    le = QLineEdit()
    le.show()
    c = ac.AddressCompleter()
    try:
        c.set_line_edit(le)
        _type_into(le, 'ali', qapp)  # user types 'ali' -> textEdited
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
        le.close()
        c.deleteLater()
        le.deleteLater()
        qapp.processEvents()


def test_programmatic_settext_does_not_trigger_completion(qapp):
    """A programmatic ``setText()`` must NOT fire the popup.

    Regression: the completer is wired to ``textEdited`` (user keystrokes
    only), not ``textChanged`` (which also fires on programmatic
    ``setText()``).  Reply/forward compose pre-populates the To field from
    the reply seed, so a ``textChanged`` trigger would instantly show the
    popup when the seeded address is already in the loaded address book —
    stealing focus from the compose editor body.  ``textEdited`` means the
    popup only appears once the user actually edits the field.
    """
    from PyQt6.QtTest import QTest
    ac._shared_addresses = ['Alice <alice@example.com>']
    le = QLineEdit()
    le.show()
    c = ac.AddressCompleter()
    try:
        c.set_line_edit(le)
        # Programmatic population (as compose does for the seeded To field):
        # completion popup/model must stay untouched.
        le.setText('Alice <alice@example.com>')
        qapp.processEvents()
        assert le.text() == 'Alice <alice@example.com>'
        assert c._model.stringList() == []  # model still empty: no popup
        assert not c.popup().isVisible()

        # A genuine user edit (textEdited) now triggers completion.
        le.setFocus()
        qapp.processEvents()
        le.selectAll()
        QTest.keyClicks(le, 'ali')  # user types 'ali' -> matches Alice
        qapp.processEvents()
        assert c._model.stringList() == ['Alice <alice@example.com>']
    finally:
        ac._shared_addresses = []
        popup = c.popup()
        if popup is not None:
            popup.hide()
        le.close()
        c.deleteLater()
        le.deleteLater()
        qapp.processEvents()


def test_preload_addresses_loads_once(qapp, monkeypatch, client_stub):
    class C:
        contacts = [{'display': 'Alice <alice@example.com>',
                     'address': 'alice@example.com', 'name': 'Alice'}]
        def get_contacts(self, query=''):
            return self.contacts

    monkeypatch.setattr(ac, '_shared_loader', None)
    monkeypatch.setattr(ac, '_shared_addresses', [])
    monkeypatch.setattr('lazarus.client.get_client', lambda: C())

    ac.preload_addresses()
    ac.preload_addresses()  # second call must be a no-op (singleton)

    deadline = time.time() + 5
    while time.time() < deadline and not ac._shared_addresses:
        qapp.processEvents()
        time.sleep(0.02)
    assert ac._shared_addresses == ['Alice <alice@example.com>']
