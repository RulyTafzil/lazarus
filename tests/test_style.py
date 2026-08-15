"""style.py — memoised cell fonts and theme colors."""
import os

import lazarus.settings as settings
from lazarus import style


def test_cell_font_returns_distinct_copies():
    f1 = style.cell_font('DejaVu Sans Mono', 12, bold=True)
    f2 = style.cell_font('DejaVu Sans Mono', 12, bold=True)
    assert f1 is not f2  # copies, never the cached base
    assert f1.bold()
    assert f2.bold()


def test_cell_font_mutation_does_not_leak():
    plain = style.cell_font('DejaVu Sans Mono', 12)
    italic = style.cell_font('DejaVu Sans Mono', 12, italic=True)
    assert not plain.italic()
    assert italic.italic()


def test_theme_color_cached_and_follows_theme_swap():
    settings.theme = {'bg': '#2e3440', 'fg': '#d8dee9'}
    c1 = style.theme_color('fg')
    c2 = style.theme_color('fg')
    assert c1 is c2
    assert c1.name() == '#d8dee9'
    # Replacing the theme dict re-parses (cache key is id(theme)).
    settings.theme = {'bg': '#000000', 'fg': '#ffffff'}
    c3 = style.theme_color('fg')
    assert c3 is not c1
    assert c3.name() == '#ffffff'


def test_theme_color_missing_key_raises():
    settings.theme = {'bg': '#2e3440'}
    try:
        style.theme_color('no_such_color')
    except KeyError:
        pass
    else:
        raise AssertionError('expected KeyError for missing theme color')


def test_nerd_font_family_resolves(qapp):
    fam = style.nerd_font_family()
    assert isinstance(fam, str) and fam
    assert style.nerd_font_family() == fam  # cached


def test_nerd_font_family_setting_wins(qapp, monkeypatch):
    monkeypatch.setattr(settings, 'nerd_font', 'My Custom Nerd Font')
    assert style.nerd_font_family() == 'My Custom Nerd Font'
    # restoring the setting re-resolves (cache keyed on the setting)
    monkeypatch.setattr(settings, 'nerd_font', '')
    assert style.nerd_font_family() != 'My Custom Nerd Font'


def test_glyph_image_writes_png_once(qapp):
    path1 = style.glyph_image('\uf078', 12, '#4c566a')
    path2 = style.glyph_image('\uf078', 12, '#4c566a')
    assert path1 == path2  # cached
    assert os.path.isfile(path1)
    with open(path1, 'rb') as f:
        assert f.read(8) == b'\x89PNG\r\n\x1a\n'  # PNG magic


def test_glyph_image_renders_nonblank(qapp):
    path = style.glyph_image('\uf032', 16, '#ffffff')
    from PyQt6.QtGui import QPixmap
    pm = QPixmap(path)
    assert not pm.isNull()
    img = pm.toImage()
    n = sum(1 for y in range(img.height()) for x in range(img.width())
            if img.pixelColor(x, y).alpha() > 0)
    assert n > 8  # the glyph actually painted, not blank
