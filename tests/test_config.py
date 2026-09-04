"""config validation and theme palette sanity."""
import lazarus.settings as settings
import lazarus.themes as themes
from lazarus.config import _validate_settings


def _reset():
    settings.email_address = 'Me <me@example.com>'
    settings.sent_dir = '~/Mail/default/Sent'
    settings.smtp_accounts = ['default']
    settings.sync_mail_interval = 300
    settings.thread_pane_position = 'right'
    settings.theme = themes.nord
    settings.filter_rules = []
    settings.log_level = 'WARNING'


def test_valid_settings_no_errors():
    _reset()
    assert _validate_settings() == []


def test_missing_email_address_is_optional():
    _reset()
    settings.email_address = ''
    # Mail identity comes from the NED daemon; the desktop config may
    # omit it entirely.
    assert not any('email_address is required' in e
                   for e in _validate_settings())


def test_email_missing_at():
    _reset()
    settings.email_address = 'meexample.com'
    assert any("missing '@'" in e for e in _validate_settings())


def test_account_missing_from_smtp_accounts():
    _reset()
    settings.email_address = {'extra': 'a@b.com'}
    assert any("not in smtp_accounts" in e for e in _validate_settings())


def test_sent_dir_tilde_typo():
    _reset()
    settings.sent_dir = {'default': '~Mail/default/Sent'}
    assert any("did you mean '~/Mail" in e for e in _validate_settings())


def test_sent_dir_none_allowed():
    _reset()
    settings.sent_dir = None
    assert _validate_settings() == []


def test_empty_smtp_accounts_is_optional():
    _reset()
    settings.smtp_accounts = []
    # Accounts come from the daemon; an empty desktop list is fine.
    assert not any('smtp_accounts is empty' in e for e in _validate_settings())


def test_bad_pane_position():
    _reset()
    settings.thread_pane_position = 'sideways'
    assert any('thread_pane_position' in e for e in _validate_settings())


def test_short_sync_interval_warns():
    _reset()
    settings.sync_mail_interval = 3
    assert any('very short' in e for e in _validate_settings())


def test_bad_log_level():
    _reset()
    settings.log_level = 'LOUD'
    assert any('log_level' in e for e in _validate_settings())


def test_bad_theme_type():
    _reset()
    settings.theme = 'nord'  # str, not dict
    assert any('theme must be a dict' in e for e in _validate_settings())


def test_theme_in_config_warns_deprecation(monkeypatch, tmp_path):
    from lazarus.config import load_config
    cfg = tmp_path / "config.py"
    cfg.write_text("import lazarus\nlazarus.settings.theme = lazarus.themes.solarized_dark\n")
    monkeypatch.setattr('lazarus.config._config_path', lambda: (str(cfg), []))
    _reset()
    path, warnings = load_config()
    assert any('deprecated' in w for w in warnings)


# -- themes ----------------------------------------------------------------

ALL_THEMES = [
    themes.nord, themes.solarized_dark, themes.solarized_light,
    themes.catppuccin_macchiato,
    themes.gruvbox_light, themes.gruvbox_light_hard, themes.gruvbox_light_soft,
    themes.gruvbox_dark, themes.gruvbox_dark_hard, themes.gruvbox_dark_soft,
]


def test_all_themes_have_required_keys():
    required = {'bg', 'fg', 'fg_dim', 'bg_alt', 'bg_button'}
    for t in ALL_THEMES:
        assert required <= set(t.keys()), t


def test_theme_colors_are_hex():
    import re
    hex_re = re.compile(r'^#[0-9a-fA-F]{6}$')
    for t in ALL_THEMES:
        for k, v in t.items():
            assert hex_re.match(v), f'{k}={v!r}'


def test_build_global_stylesheet_renders():
    css = themes.build_global_stylesheet(themes.nord)
    assert 'QTreeView' in css
    assert '{' in css and '}' in css


def test_apply_theme_sets_style(qapp):
    themes.apply_theme(themes.nord)
    inst = __import__('PyQt6.QtWidgets', fromlist=['QApplication']).QApplication.instance()
    assert inst is not None
