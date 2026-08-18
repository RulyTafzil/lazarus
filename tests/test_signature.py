"""signature — per-account signature file loading."""
from lazarus import signature


def test_load_plaintext(monkeypatch, tmp_path):
    d = tmp_path / 'default'
    d.mkdir()
    (d / 'signature').write_text('-- \nRuly\n')
    monkeypatch.setattr(signature, 'config_dir', lambda account: str(d))

    text, html = signature.load('default')

    assert text == '-- \nRuly\n'
    assert html is None


def test_load_html_only_falls_back_to_text(monkeypatch, tmp_path):
    d = tmp_path / 'default'
    d.mkdir()
    (d / 'signature.html').write_text('<p>Hi</p>')
    monkeypatch.setattr(signature, 'config_dir', lambda account: str(d))
    # html2text shells out to w3m — patch it so the test needs no tools.
    monkeypatch.setattr(signature.util, 'html2text', lambda s: 'PLAIN:' + s)

    text, html = signature.load('default')

    assert html == '<p>Hi</p>'
    assert text == 'PLAIN:<p>Hi</p>'


def test_load_missing_returns_none(monkeypatch, tmp_path):
    d = tmp_path / 'default'
    d.mkdir()
    monkeypatch.setattr(signature, 'config_dir', lambda account: str(d))

    text, html = signature.load('default')

    assert text is None and html is None


def test_load_ignores_unreadable_file(monkeypatch, tmp_path):
    d = tmp_path / 'default'
    d.mkdir()
    bad = d / 'signature'
    bad.write_text('x')
    bad.chmod(0)
    monkeypatch.setattr(signature, 'config_dir', lambda account: str(d))

    text, html = signature.load('default')

    assert text is None and html is None
