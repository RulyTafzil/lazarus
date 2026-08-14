"""style.py — memoised cell fonts and theme colors."""
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
