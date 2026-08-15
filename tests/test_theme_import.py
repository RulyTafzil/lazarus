"""Terminal-style theme import: mapping heuristic, pack loading, registry."""
import json

import lazarus.settings as settings
import lazarus.themes as themes

# The complete set of keys the app reads from a theme dict. Kept in sync
# with themes.THEME_KEYS -- the mapping must emit all of them or the app
# crashes (KeyError 'fg_subject_unread' on opening a thread).
REQUIRED_KEYS = themes.THEME_KEYS

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
    assert set(REQUIRED_KEYS) <= set(mapped), (
        f"missing {sorted(set(REQUIRED_KEYS) - set(mapped))}")
    for key in REQUIRED_KEYS:
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

    mapped, errors, _raw = themes.load_theme_pack(pack_path)
    assert 'Dracula' in mapped
    assert 'Broken' not in mapped
    assert any('Broken' in e or '[1]' in e for e in errors)


def test_load_theme_pack_missing_file():
    mapped, errors, _raw = themes.load_theme_pack('/nonexistent/pack.json')
    assert mapped == {}
    assert errors


def test_load_theme_pack_not_a_list(tmp_path):
    pack_path = tmp_path / 'pack.json'
    pack_path.write_text(json.dumps({'name': 'oops'}))
    mapped, errors, _raw = themes.load_theme_pack(pack_path)
    assert mapped == {}
    assert any('list' in e for e in errors)


# --- registry assembly ---------------------------------------------------

def test_registry_includes_hand_written_and_bundled_themes():
    _reset()
    registry = themes.build_registry()
    assert registry['nord'] is themes.nord          # hand-written, untouched
    assert len(registry) > 500                        # bundled pack is large


def test_bundled_pack_entries_map_completely():
    """Every bundled theme must map to a complete theme dict.

    Regression: the mapping used to emit 12 keys while the app reads
    more (fg_subject_unread, fg_subject_irrelevant, fg_tags ...) --
    opening a thread under any imported theme crashed with
    KeyError 'fg_subject_unread'.
    """
    mapped, errors, _raw = themes.load_theme_pack(themes._builtin_pack_path())
    assert not errors, errors[:3]
    assert len(mapped) >= 600
    incomplete = {
        name: sorted(set(REQUIRED_KEYS) - set(theme))
        for name, theme in mapped.items()
        if not set(REQUIRED_KEYS) <= set(theme)
    }
    assert not incomplete, (
        f"{len(incomplete)} incomplete mappings, e.g. "
        f"{list(incomplete.items())[:2]}")


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


# --- override value forms (hex / palette index / named / lazarus key) ---

def _override(keys):
    _reset()
    settings.theme_overrides = {'Dracula': keys}
    registry = {'Dracula': themes.terminal_theme_to_lazarus(DRACULA_ENTRY)}
    raw = {'Dracula': DRACULA_ENTRY}
    return registry, raw


def test_override_palette_index_resolves_to_source_hex():
    registry, raw = _override({'fg_subject_unread': 3})
    themes._apply_overrides(registry, raw)
    assert registry['Dracula']['fg_subject_unread'] == DRACULA_ENTRY['palette']['3']


def test_override_palette_index_str_form():
    registry, raw = _override({'fg_subject_unread': '11'})
    themes._apply_overrides(registry, raw)
    assert registry['Dracula']['fg_subject_unread'] == DRACULA_ENTRY['palette']['11']


def test_override_named_terminal_colors():
    registry, raw = _override({
        'fg_tags': 'foreground',
        'bg_alt': 'selection-background',
        'fg': 'cursor-color',
    })
    themes._apply_overrides(registry, raw)
    assert registry['Dracula']['fg_tags'] == DRACULA_ENTRY['foreground']
    assert registry['Dracula']['bg_alt'] == DRACULA_ENTRY['selection-background']
    assert registry['Dracula']['fg'] == DRACULA_ENTRY['cursor-color']


def test_override_references_another_lazarus_key():
    registry, raw = _override({'fg_date': 'fg_dim'})
    themes._apply_overrides(registry, raw)
    assert registry['Dracula']['fg_date'] == registry['Dracula']['fg_dim']


def test_override_missing_named_color_falls_back():
    entry = dict(DRACULA_ENTRY)
    del entry['cursor-color']
    registry, raw = _override({'fg': 'cursor-color'})
    themes._apply_overrides(registry, {'Dracula': entry})
    assert registry['Dracula']['fg'] == entry['foreground']


def test_override_unresolvable_value_warns_and_skips(caplog):
    registry, raw = _override({'fg_subject_unread': 99, 'fg_link': '#000000'})
    with caplog.at_level('WARNING', logger='lazarus.themes'):
        themes._apply_overrides(registry, raw)
    # 99 is out of range: skipped, hex still applies
    assert registry['Dracula']['fg_subject_unread'] != '99'
    assert registry['Dracula']['fg_link'] == '#000000'
    assert any('skipping' in r.message for r in caplog.records)


def test_override_hand_written_theme_rejects_palette_refs(caplog):
    """Hand-written themes have no source entry -- palette/named
    references can't resolve; hex overrides still apply."""
    _reset()
    settings.theme_overrides = {'nord': {'fg_link': 'foreground'}}
    registry = {'nord': dict(themes.nord)}
    with caplog.at_level('WARNING', logger='lazarus.themes'):
        themes._apply_overrides(registry)  # no raw entries
    assert registry['nord']['fg_link'] == themes.nord['fg_link']  # untouched
    assert any('skipping' in r.message for r in caplog.records)


def test_build_registry_resolves_palette_overrides(tmp_path, monkeypatch):
    """End to end: a palette-index override lands as the source hex in
    the assembled registry."""
    _reset()
    settings.theme_overrides = {'Dracula': {'fg_subject_unread': 3}}
    pack_path = tmp_path / 'mine.json'
    pack_path.write_text(json.dumps([DRACULA_ENTRY]))
    monkeypatch.setattr(themes, '_user_theme_dir', lambda: tmp_path)

    registry = themes.build_registry()
    assert registry['Dracula']['fg_subject_unread'] == DRACULA_ENTRY['palette']['3']
