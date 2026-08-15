"""Terminal-style theme import: mapping heuristic, pack loading, registry."""
import json

import lazarus.settings as settings
import lazarus.themes as themes

REQUIRED_KEYS = [
    'bg', 'bg_alt', 'fg', 'fg_dim', 'fg_bright', 'fg_good', 'fg_bad',
    'bg_button', 'fg_button', 'fg_link', 'bg_highlight', 'fg_highlight',
]

DRACULA_ENTRY = {
    'name': 'Dracula',
    'background': '#282a36',
    'foreground': '#f8f8f2',
    'cursor-color': '#f8f8f2',
    'selection-background': '#44475a',
    'selection-foreground': '#ffffff',
    'palette': {
        '0': '#21222c', '1': '#ff5555', '2': '#50fa7b', '3': '#f1fa8c',
        '4': '#bd93f9', '5': '#ff79c6', '6': '#8be9fd', '7': '#f8f8f2',
        '8': '#6272a4', '9': '#ff6e6e', '10': '#69ff94', '11': '#ffffa5',
        '12': '#d6acff', '13': '#ff92df', '14': '#a4ffff', '15': '#ffffff',
    },
}


def _reset():
    settings.theme_overrides = {}


# --- mapping heuristic ------------------------------------------------

def test_mapping_produces_all_required_keys():
    mapped = themes.terminal_theme_to_lazarus(DRACULA_ENTRY)
    for key in REQUIRED_KEYS:
        assert key in mapped, f"missing {key}"
        assert mapped[key]  # non-empty


def test_mapping_uses_background_and_foreground_directly():
    mapped = themes.terminal_theme_to_lazarus(DRACULA_ENTRY)
    assert mapped['bg'] == '#282a36'
    assert mapped['fg'] == '#f8f8f2'


def test_mapping_prefers_selection_colors_for_highlight():
    mapped = themes.terminal_theme_to_lazarus(DRACULA_ENTRY)
    assert mapped['bg_highlight'] == '#44475a'
    assert mapped['fg_highlight'] == '#ffffff'


def test_mapping_falls_back_when_no_selection_colors():
    entry = dict(DRACULA_ENTRY)
    entry.pop('selection-background', None)
    entry.pop('selection-foreground', None)
    mapped = themes.terminal_theme_to_lazarus(entry)
    # falls back to palette blue / bg, not a crash or missing key
    assert mapped['bg_highlight']
    assert mapped['fg_highlight'] == entry['background']


def test_mapping_dark_background_lightens_bg_alt():
    mapped = themes.terminal_theme_to_lazarus(DRACULA_ENTRY)
    from PyQt6.QtGui import QColor
    assert QColor(mapped['bg_alt']).lightness() > QColor(mapped['bg']).lightness()


def test_mapping_light_background_darkens_bg_alt():
    entry = dict(DRACULA_ENTRY)
    entry['background'] = '#f7f7f7'
    entry['foreground'] = '#222222'
    mapped = themes.terminal_theme_to_lazarus(entry)
    from PyQt6.QtGui import QColor
    assert QColor(mapped['bg_alt']).lightness() < QColor(mapped['bg']).lightness()


# --- validation ---------------------------------------------------------

def test_validate_accepts_well_formed_entry():
    assert themes._validate_terminal_entry(DRACULA_ENTRY, 'test') == []


def test_validate_flags_missing_required_key():
    entry = dict(DRACULA_ENTRY)
    del entry['background']
    errors = themes._validate_terminal_entry(entry, 'test')
    assert any('background' in e for e in errors)


def test_validate_flags_incomplete_palette():
    entry = json.loads(json.dumps(DRACULA_ENTRY))  # deep copy
    del entry['palette']['15']
    errors = themes._validate_terminal_entry(entry, 'test')
    assert any('15' in e for e in errors)


def test_validate_flags_non_dict_palette():
    entry = dict(DRACULA_ENTRY)
    entry['palette'] = 'not-a-dict'
    errors = themes._validate_terminal_entry(entry, 'test')
    assert any('object' in e for e in errors)


# --- pack loading (malformed entries don't take down the whole file) ---

def test_load_theme_pack_skips_bad_entries(tmp_path):
    good = dict(DRACULA_ENTRY)
    bad = {'name': 'Broken'}  # missing everything
    pack_path = tmp_path / 'pack.json'
    pack_path.write_text(json.dumps([good, bad]))

    mapped, errors = themes.load_theme_pack(pack_path)
    assert 'Dracula' in mapped
    assert 'Broken' not in mapped
    assert any('Broken' in e or '[1]' in e for e in errors)


def test_load_theme_pack_missing_file():
    mapped, errors = themes.load_theme_pack('/nonexistent/pack.json')
    assert mapped == {}
    assert errors


def test_load_theme_pack_not_a_list(tmp_path):
    pack_path = tmp_path / 'pack.json'
    pack_path.write_text(json.dumps({'name': 'oops'}))
    mapped, errors = themes.load_theme_pack(pack_path)
    assert mapped == {}
    assert any('list' in e for e in errors)


# --- registry assembly ---------------------------------------------------

def test_registry_includes_hand_written_and_bundled_themes():
    _reset()
    registry = themes.build_registry()
    assert registry['nord'] is themes.nord          # hand-written, untouched
    assert len(registry) > 500                        # bundled pack is large


def test_registry_hand_written_theme_wins_on_name_collision(tmp_path, monkeypatch):
    _reset()
    # a user pack claiming to define "nord" should NOT override the
    # hand-written Python theme
    fake_nord = dict(DRACULA_ENTRY)
    fake_nord['name'] = 'nord'
    pack_path = tmp_path / 'evil.json'
    pack_path.write_text(json.dumps([fake_nord]))
    monkeypatch.setattr(themes, '_user_theme_dir', lambda: tmp_path)

    registry = themes.build_registry()
    assert registry['nord'] is themes.nord


def test_registry_loads_user_pack_theme(tmp_path, monkeypatch):
    _reset()
    pack_path = tmp_path / 'mine.json'
    pack_path.write_text(json.dumps([DRACULA_ENTRY]))
    monkeypatch.setattr(themes, '_user_theme_dir', lambda: tmp_path)

    registry = themes.build_registry()
    assert 'Dracula' in registry
    assert registry['Dracula']['bg'] == '#282a36'


# --- overrides -----------------------------------------------------------

def test_overrides_apply_specific_keys_only():
    _reset()
    settings.theme_overrides = {
        'Dracula': {'fg_link': '#8be9fd'},
    }
    registry = {'Dracula': themes.terminal_theme_to_lazarus(DRACULA_ENTRY)}
    original_bg = registry['Dracula']['bg']
    themes._apply_overrides(registry)
    assert registry['Dracula']['fg_link'] == '#8be9fd'
    assert registry['Dracula']['bg'] == original_bg  # untouched


def test_overrides_unknown_theme_name_does_not_raise():
    _reset()
    settings.theme_overrides = {'Nonexistent Theme': {'fg_link': '#000000'}}
    registry = {'Dracula': themes.terminal_theme_to_lazarus(DRACULA_ENTRY)}
    themes._apply_overrides(registry)  # should not raise
    assert 'Nonexistent Theme' not in registry


def test_no_overrides_is_a_noop():
    _reset()
    registry = {'Dracula': themes.terminal_theme_to_lazarus(DRACULA_ENTRY)}
    before = dict(registry['Dracula'])
    themes._apply_overrides(registry)
    assert registry['Dracula'] == before
