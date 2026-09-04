"""signature — per-account signature file loading (ned-only)."""
from ned import signature


def test_load_plaintext(monkeypatch, tmp_path):
    d = tmp_path / 'default'
    d.mkdir()
    (d / 'signature').write_text('-- \nRuly\n')
    monkeypatch.setattr(signature, 'account_dir', lambda account: str(d))

    text, html = signature.load('default')

    assert text == '-- \nRuly\n'
    assert html is None


def test_load_html_only_falls_back_to_text(monkeypatch, tmp_path):
    d = tmp_path / 'default'
    d.mkdir()
    (d / 'signature.html').write_text('<p>Hi</p>')
    monkeypatch.setattr(signature, 'account_dir', lambda account: str(d))
    # html2text shells out to w3m — patch it so the test needs no tools.
    monkeypatch.setattr(signature.util, 'html2text', lambda s: 'PLAIN:' + s)

    text, html = signature.load('default')

    assert html == '<p>Hi</p>'
    assert text == 'PLAIN:<p>Hi</p>'


def test_load_missing_returns_none(monkeypatch, tmp_path):
    d = tmp_path / 'default'
    d.mkdir()
    monkeypatch.setattr(signature, 'account_dir', lambda account: str(d))

    text, html = signature.load('default')

    assert text is None and html is None


def test_load_ignores_unreadable_file(monkeypatch, tmp_path):
    d = tmp_path / 'default'
    d.mkdir()
    bad = d / 'signature'
    bad.write_text('x')
    bad.chmod(0)
    monkeypatch.setattr(signature, 'account_dir', lambda account: str(d))

    text, html = signature.load('default')

    assert text is None and html is None


def test_config_dir_ned_only(monkeypatch, tmp_path):
    """NED reads ~/.config/ned only — never follows the lazarus config dir."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    # Signature present under the desktop's lazarus dir must NOT be found.
    laz_dir = tmp_path / "lazarus" / "work"
    laz_dir.mkdir(parents=True)
    (laz_dir / "signature").write_text("Sig in lazarus")

    assert signature.account_dir("work") == str(tmp_path / "ned" / "work")
    assert signature.load("work") == (None, None)

    ned_dir = tmp_path / "ned" / "work"
    ned_dir.mkdir(parents=True)
    (ned_dir / "signature").write_text("Sig in ned")

    assert signature.load("work") == ("Sig in ned", None)