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


def test_config_dir_ned_priority(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("PyQt6.QtCore.QStandardPaths.writableLocation", lambda _: "")

    laz_dir = tmp_path / "lazarus" / "work"
    laz_dir.mkdir(parents=True)
    assert signature.config_dir("work") == str(laz_dir)

    ned_dir = tmp_path / "ned" / "work"
    ned_dir.mkdir(parents=True)
    assert signature.config_dir("work") == str(ned_dir)


def test_signature_load_ned_and_lazarus(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("PyQt6.QtCore.QStandardPaths.writableLocation", lambda _: "")

    laz_dir = tmp_path / "lazarus" / "contact"
    laz_dir.mkdir(parents=True)
    (laz_dir / "signature").write_text("Sig in lazarus")

    text, html = signature.load("contact")
    assert text == "Sig in lazarus"

    ned_dir = tmp_path / "ned" / "contact"
    ned_dir.mkdir(parents=True)
    (ned_dir / "signature").write_text("Sig in ned")

    text, html = signature.load("contact")
    assert text == "Sig in ned"

