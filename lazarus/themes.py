#     Lazarus - A fork of Dodo, a graphical, hackable email client based on notmuch
#     Copyright (C) 2021 - Aleks Kissinger
#     Copyright (C) 2025 - Ruly Tafzil
#
# This file is part of Lazarus
#
# Lazarus is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Lazarus is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Lazarus. If not, see <https://www.gnu.org/licenses/>.
from __future__ import annotations
import json
import logging
from pathlib import Path

from PyQt6.QtCore import QStandardPaths
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtWidgets import QApplication

from . import settings as _settings  # for hover blend toward theme fg

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Global stylesheet helpers — thin modern scrollbars + rounded Fusion widgets
# ---------------------------------------------------------------------------

def _is_dark(theme: dict) -> bool:
    return QColor(theme['bg']).lightness() < 128


def _border_color(theme: dict) -> str:
    """Border color: bg_alt if distinct from bg, else a subtle step."""
    bg = theme['bg']
    bg_alt = theme.get('bg_alt', bg)
    if QColor(bg_alt).name().lower() != QColor(bg).name().lower():
        return bg_alt
    c = QColor(bg)
    return c.lighter(125).name() if _is_dark(theme) else c.darker(110).name()


def _thumb_color(theme: dict) -> str:
    """Scrollbar thumb: theme-derived, high enough contrast to see.

    Dark themes: bg_button is usually the right muted tone.
    Light themes: bg_button is often nearly identical to bg (e.g.
    solarized_light), so fg_dim is far more visible.
    """
    bg = theme['bg']
    is_dark = _is_dark(theme)
    if is_dark:
        bg_button = theme.get('bg_button', theme.get('bg_alt', bg))
        if QColor(bg_button).name().lower() != QColor(bg).name().lower():
            if abs(QColor(bg_button).lightness() - QColor(bg).lightness()) > 10:
                return bg_button
        return theme.get('fg_dim', theme['fg'])
    else:
        # light background — fg_dim (a muted dark) is reliably visible
        return theme.get('fg_dim', theme.get('bg_button', theme['fg']))


def _thumb_hover_color(base_hex: str, theme: dict) -> str:
    c = QColor(base_hex)
    if not c.isValid():
        return base_hex
    # Hover must stay clearly distinguishable from both the transparent track
    # (viewport bg) and the idle thumb. For light themes blending toward fg
    # is invisible when thumb == fg_dim — use a darker step instead. For
    # dark themes, blending toward fg (which is light) gives good contrast.
    is_dark = _is_dark(theme)
    if is_dark:
        fg = theme.get('fg', _settings.theme.get('fg', '#ffffff'))
        fg_c = QColor(fg)
        r = int(c.red()   * 0.6 + fg_c.red()   * 0.4)
        g = int(c.green() * 0.6 + fg_c.green() * 0.4)
        b = int(c.blue()  * 0.6 + fg_c.blue()  * 0.4)
        return QColor(r, g, b).name()
    else:
        # light bg, dark thumb — darker toward bg_dim-like depth
        return c.darker(125).name()


def build_global_stylesheet(theme: dict) -> str:
    """Return application-wide QSS for Fusion: thin scrollbars + rounded panels.

    All colors are derived from *theme* so every existing palette (Nord,
    Solarized, Gruvbox, Catppuccin) adapts without per-theme tuning.
    Called from :func:`apply_theme`.
    """
    bg = theme['bg']

    thumb = _thumb_color(theme)
    thumb_hover = _thumb_hover_color(thumb, theme)
    header_bg = theme.get('bg_alt', bg)

    return f"""
/* ── Header row spans full width (paired with HeaderInsetTreeView) ── */
QHeaderView {{
    background: {header_bg};
    border: none;
}}
QHeaderView::section {{
    background: {header_bg};
    border: none;
    padding: 2px 4px;
}}
QTreeView {{
    show-decoration-selected: 1;
}}
QTreeView::item {{
    padding-left: 4px;
    padding-right: 4px;
    padding-top: 1px;
    padding-bottom: 1px;
}}
/* Remove default tree indentation so date column aligns with header (~2-4px), not 20px */
QTreeView::branch {{
    border: none;
    background: {bg};
}}
/* Compose gaps: header row paints header_bg via compose.py bar stylesheet;
   panel itself stays at palette Window (bg) so no QSS needed here. */
/* ── Thin modern scrollbars — pill handle, bg track so pill floats ── */
QScrollBar:vertical {{
    background: {bg};
    width: 8px;
    margin: 0px;
    border: none;
}}
QScrollBar:horizontal {{
    background: {bg};
    height: 8px;
    margin: 0px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {thumb};
    min-height: 28px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal {{
    background: {thumb};
    min-width: 28px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
    background: {thumb_hover};
}}
QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed {{
    background: {thumb_hover};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    height: 0px;
    width: 0px;
    border: none;
    background: none;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: {bg};
}}
QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical,
QScrollBar::left-arrow:horizontal, QScrollBar::right-arrow:horizontal {{
    border: none;
    background: none;
}}
"""


# palettes used in theme definitions
nord_p = {
  'polar0':  '#2e3440',
  'polar1':  '#3b4252',
  'polar2':  '#434c5e',
  'polar3':  '#4c566a',
  'snow0':   '#d8dee9',
  'snow1':   '#e5e9f0',
  'snow2':   '#eceff4',
  'frost0':  '#8fbcbb',
  'frost1':  '#88c0d0',
  'frost2':  '#81a1c1',
  'frost3':  '#5e81ac',
  'aurora0': '#bf616a',
  'aurora1': '#d08770',
  'aurora2': '#ebcb8b',
  'aurora3': '#a3be8c',
  'aurora4': '#b48ead',
}

solarized_p = {
  'base03':    '#002b36',
  'base02':    '#073642',
  'base01':    '#586e75',
  'base00':    '#657b83',
  'base0':     '#839496',
  'base1':     '#93a1a1',
  'base2':     '#eee8d5',
  'base3':     '#fdf6e3',
  'yellow':    '#b58900',
  'orange':    '#cb4b16',
  'red':       '#dc322f',
  'magenta':   '#d33682',
  'violet':    '#6c71c4',
  'blue':      '#268bd2',
  'cyan':      '#2aa198',
  'green':     '#859900',
}

cat_macchiato_p = {
  'rosewater':  '#f4dbd6',
  'flamingo':   '#f0c6c6',
  'pink':       '#f5bde6',
  'mauve':      '#c6a0f6',
  'red':        '#ed8796',
  'maroon':     '#ee99a0',
  'peach':      '#f5a97f',
  'yellow':     '#eed49f',
  'green':      '#a6da95',
  'teal':       '#8bd5ca',
  'sky':        '#91d7e3',
  'sapphire':   '#7dc4e4',
  'blue':       '#8aadf4',
  'lavender':   '#b7bdf8',
  'text':       '#cad3f5',
  'subtext1':   '#b8c0e0',
  'subtext0':   '#a5adcb',
  'overlay2':   '#939ab7',
  'overlay1':   '#8087a2',
  'overlay0':   '#6e738d',
  'surface2':   '#5b6078',
  'surface1':   '#494d64',
  'surface0':   '#363a4f',
  'base':       '#24273a',
  'mantle':     '#1e2030',
  'crust':      '#181926',
}

gruvbox_p = {
  'dark0_hard':     '#1d2021',
  'dark0':          '#282828',
  'dark0_soft':     '#32302f',
  'dark1':          '#3c3836',
  'dark2':          '#504945',
  'dark3':          '#665c54',
  'dark4':          '#7c6f64',

  'gray_245':       '#928374',
  'gray_244':       '#928374',

  'light0_hard':    '#f9f5d7',
  'light0':         '#fbf1c7',
  'light0_soft':    '#f2e5bc',
  'light1':         '#ebdbb2',
  'light2':         '#d5c4a1',
  'light3':         '#bdae93',
  'light4':         '#a89984',

  'bright_red':     '#fb4934',
  'bright_green':   '#b8bb26',
  'bright_yellow':  '#fabd2f',
  'bright_blue':    '#83a598',
  'bright_purple':  '#d3869b',
  'bright_aqua':    '#8ec07c',
  'bright_orange':  '#fe8019',

  'neutral_red':    '#cc241d',
  'neutral_green':  '#98971a',
  'neutral_yellow': '#d79921',
  'neutral_blue':   '#458588',
  'neutral_purple': '#b16286',
  'neutral_aqua':   '#689d6a',
  'neutral_orange': '#d65d0e',

  'faded_red':      '#9d0006',
  'faded_green':    '#79740e',
  'faded_yellow':   '#b57614',
  'faded_blue':     '#076678',
  'faded_purple':   '#8f3f71',
  'faded_aqua':     '#427b58',
  'faded_orange':   '#af3a03',
}

catppuccin_macchiato = {
  'bg': cat_macchiato_p['base'],
  'fg': cat_macchiato_p['text'],
  'fg_bright': cat_macchiato_p['lavender'],
  'fg_dim': cat_macchiato_p['overlay1'],
  'fg_good': cat_macchiato_p['green'],
  'fg_bad': cat_macchiato_p['red'],
  'bg_alt': cat_macchiato_p['crust'],
  'bg_button': cat_macchiato_p['surface0'],
  'fg_button': cat_macchiato_p['rosewater'],
  'fg_link': cat_macchiato_p['blue'],
  'bg_highlight': cat_macchiato_p['blue'],
  'fg_highlight': cat_macchiato_p['crust'],
  'fg_subject': cat_macchiato_p['text'],
  'fg_subject_irrelevant': cat_macchiato_p['overlay2'],
  'fg_subject_unread': cat_macchiato_p['mauve'],
  'fg_subject_flagged': cat_macchiato_p['yellow'],
  'fg_from': cat_macchiato_p['blue'],
  'fg_date': cat_macchiato_p['flamingo'],
  'fg_tags': cat_macchiato_p['peach'],
}
"""Theme based on the `Catppuchin`_ palette (macchiatto version).

.. _Catppuchin: https://github.com/catppuccin/catppuccin
"""

solarized_dark = {
  'bg': solarized_p['base02'],
  'fg': solarized_p['base1'],
  'fg_bright': solarized_p['violet'],
  'fg_dim': solarized_p['base01'],
  'fg_good': solarized_p['green'],
  'fg_bad': solarized_p['red'],
  'bg_alt': solarized_p['base03'],
  'bg_button': solarized_p['base03'],
  'fg_button': solarized_p['base01'],
  'fg_link': solarized_p['violet'],
  'bg_highlight': solarized_p['base2'],
  'fg_highlight': solarized_p['base01'],
  'fg_subject': solarized_p['base0'],
  'fg_subject_irrelevant': solarized_p['base01'],
  'fg_subject_unread': solarized_p['base2'],
  'fg_subject_flagged': solarized_p['violet'],
  'fg_from': solarized_p['blue'],
  'fg_date': solarized_p['cyan'],
  'fg_tags': solarized_p['violet'],
}
"""Theme based on the `Solarized`_ palette (dark background).

.. _Solarized: https://ethanschoonover.com/solarized/
"""

solarized_light = {
  'bg': solarized_p['base3'],
  'fg': solarized_p['base01'],
  'fg_bright': solarized_p['violet'],
  'fg_dim': solarized_p['base1'],
  'fg_good': solarized_p['green'],
  'fg_bad': solarized_p['red'],
  'bg_alt': solarized_p['base3'],
  'bg_button': solarized_p['base3'],
  'fg_button': solarized_p['base1'],
  'fg_link': solarized_p['violet'],
  'bg_highlight': solarized_p['base02'],
  'fg_highlight': solarized_p['base1'],
  'fg_subject': solarized_p['base0'],
  'fg_subject_irrelevant': solarized_p['base01'],
  'fg_subject_unread': solarized_p['base02'],
  'fg_subject_flagged': solarized_p['violet'],
  'fg_from': solarized_p['blue'],
  'fg_date': solarized_p['cyan'],
  'fg_tags': solarized_p['violet'],
}
"""Theme based on the `Solarized`_ palette (light background).

.. _Solarized: https://ethanschoonover.com/solarized/
"""

nord = {
  'bg': nord_p['polar0'],
  'fg': nord_p['snow0'],
  'fg_bright': nord_p['aurora4'],
  'fg_dim': nord_p['polar3'],
  'fg_good': nord_p['aurora3'],
  'fg_bad': nord_p['aurora0'],
  'bg_alt': nord_p['polar1'],
  'bg_button': nord_p['polar3'],
  'fg_button': nord_p['snow2'],
  'fg_link': nord_p['frost2'],
  'bg_highlight': nord_p['aurora3'],
  'fg_highlight': nord_p['polar0'],
  'fg_subject': nord_p['snow0'],
  'fg_subject_irrelevant': nord_p['polar3'],
  'fg_subject_unread': nord_p['aurora4'],
  'fg_subject_flagged': nord_p['aurora2'],
  'fg_from': nord_p['frost3'],
  'fg_date': nord_p['polar3'],
  'fg_tags': nord_p['frost2'],
}
"""Theme based on the `Nord`_ palette

.. _Nord: https://www.nordtheme.com/
"""

gruvbox_light = {
  'bg': gruvbox_p['light0'],
  'fg': gruvbox_p['dark1'],
  'fg_bright': gruvbox_p['dark0'],
  'fg_dim': gruvbox_p['dark2'],
  'fg_good': gruvbox_p['neutral_green'],
  'fg_bad': gruvbox_p['neutral_red'],
  'bg_alt': gruvbox_p['light1'],
  'bg_button': gruvbox_p['light1'],
  'fg_button': gruvbox_p['dark2'],
  'fg_link': gruvbox_p['neutral_purple'],
  'bg_highlight': gruvbox_p['light0'],
  'fg_highlight': gruvbox_p['neutral_yellow'],
  'fg_subject': gruvbox_p['dark3'],
  'fg_subject_irrelevant': gruvbox_p['gray_244'],
  'fg_subject_unread': gruvbox_p['neutral_green'],
  'fg_subject_flagged': gruvbox_p['neutral_orange'],
  'fg_from': gruvbox_p['neutral_blue'],
  'fg_date': gruvbox_p['neutral_aqua'],
  'fg_tags': gruvbox_p['neutral_purple'],
}
"""Theme based on the `Gruvbox`_ palette (light background)

.. _Gruvbox: https://github.com/morhetz/gruvbox
"""

gruvbox_light_hard = gruvbox_light.copy()
"""Theme based on the `Gruvbox`_ palette (light background, hard contrast)

.. _Gruvbox: https://github.com/morhetz/gruvbox
"""

gruvbox_light_soft = gruvbox_light.copy()
"""Theme based on the `Gruvbox`_ palette (light background, soft contrast)

.. _Gruvbox: https://github.com/morhetz/gruvbox
"""

gruvbox_light_hard['bg'] = gruvbox_p['light0_hard']
gruvbox_light_soft['bg'] = gruvbox_p['light0_soft']

gruvbox_dark = gruvbox_light.copy()
"""Theme based on the `Gruvbox`_ palette (dark background)

.. _Gruvbox: https://github.com/morhetz/gruvbox
"""
gruvbox_dark.update({
  'bg': gruvbox_p['dark0'],
  'fg': gruvbox_p['light1'],
  'fg_bright': gruvbox_p['light0'],
  'fg_dim': gruvbox_p['light4'],
  'bg_alt': gruvbox_p['dark1'],
  'bg_button': gruvbox_p['dark1'],
  'fg_button': gruvbox_p['light2'],
  'bg_highlight': gruvbox_p['dark0'],
  'fg_subject': gruvbox_p['light3'],
})

gruvbox_dark_hard = gruvbox_dark.copy()
"""Theme based on the `Gruvbox`_ palette (dark background, hard contrast)

.. _Gruvbox: https://github.com/morhetz/gruvbox
"""

gruvbox_dark_soft = gruvbox_dark.copy()
"""Theme based on the `Gruvbox`_ palette (dark background, soft contrast)

.. _Gruvbox: https://github.com/morhetz/gruvbox
"""

gruvbox_dark_hard['bg'] = gruvbox_p['dark0_hard']
gruvbox_dark_soft['bg'] = gruvbox_p['dark0_soft']


# ---------------------------------------------------------------------------
# Terminal-style theme import (Ghostty / iTerm2-Color-Schemes and similar)
# ---------------------------------------------------------------------------
#
# A "terminal-style" theme entry has 16 numbered ANSI colors plus a handful
# of named colors (background/foreground/cursor/selection) -- the shape
# used by Ghostty, Alacritty, iTerm2, Windows Terminal, etc. It has no
# concept of Lazarus's semantic keys (fg_link, bg_highlight, ...), so
# importing one is a best-effort heuristic mapping, not a lossless
# translation. `settings.theme_overrides` lets a user hand-correct specific
# keys per theme name without editing the source pack.
#
# Expected shape of one entry (extra/missing optional keys are tolerated):
#   {
#     "name": "Dracula",
#     "background": "#282a36",
#     "foreground": "#f8f8f2",
#     "cursor-color": "#f8f8f2",           # optional
#     "selection-background": "#44475a",   # optional
#     "selection-foreground": "#ffffff",   # optional
#     "palette": {"0": "#21222c", "1": "#ff5555", ..., "15": "#ffffff"}
#   }

_REQUIRED_TERMINAL_KEYS = ('name', 'background', 'foreground', 'palette')
_REQUIRED_PALETTE_INDICES = [str(i) for i in range(16)]


def _validate_terminal_entry(entry: dict, source: str) -> list[str]:
    """Return a list of human-readable problems with *entry*, or []."""
    errors = []
    for key in _REQUIRED_TERMINAL_KEYS:
        if key not in entry:
            errors.append(f"{source}: missing required key '{key}'")
    palette = entry.get('palette')
    if isinstance(palette, dict):
        missing_idx = [i for i in _REQUIRED_PALETTE_INDICES if i not in palette]
        if missing_idx:
            name = entry.get('name', '?')
            errors.append(
                f"{source}: theme '{name}' palette missing indices "
                f"{', '.join(missing_idx)}")
    elif 'palette' in entry:
        name = entry.get('name', '?')
        errors.append(f"{source}: theme '{name}' 'palette' must be an object")
    return errors


def terminal_theme_to_lazarus(entry: dict) -> dict:
    """Heuristically map a terminal-style theme entry to a Lazarus theme
    dict. Caller is expected to have validated *entry* already.

    This is a best-effort default, not a precise translation -- terminal
    palettes have no notion of e.g. "link color" or "highlight color".
    Use `settings.theme_overrides[name]` to hand-correct specific keys.
    """
    pal = entry['palette']
    bg = entry['background']
    fg = entry['foreground']
    is_dark = QColor(bg).lightness() < 128

    def pick(*indices: str, default: str) -> str:
        for i in indices:
            if i in pal:
                return pal[i]
        return default

    theme = {
        'bg': bg,
        'fg': fg,
        'fg_dim': pick('8', default=fg),                    # bright black
        'fg_bright': pick('15', '7', default=fg),            # bright/plain white
        'fg_good': pick('10', '2', default=fg),               # bright/plain green
        'fg_bad': pick('9', '1', default=fg),                  # bright/plain red
        'fg_link': pick('12', '4', '14', '6', default=fg),    # blue, else cyan
        'fg_button': fg,
        'bg_highlight': entry.get('selection-background') or pick('4', default=fg),
        'fg_highlight': entry.get('selection-foreground') or bg,
    }
    bg_c = QColor(bg)
    theme['bg_alt'] = bg_c.lighter(125).name() if is_dark else bg_c.darker(106).name()
    theme['bg_button'] = bg_c.lighter(150).name() if is_dark else bg_c.darker(112).name()
    return theme


def _builtin_pack_path() -> Path:
    return Path(__file__).parent / 'theme_packs' / 'builtin.json'


def _user_theme_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.ConfigLocation)
    return Path(base) / 'lazarus' / 'themes'


def load_theme_pack(path: Path | str) -> tuple[dict[str, dict], list[str]]:
    """Load one JSON theme-pack file (a list of terminal-style entries).

    Returns (mapped_themes, errors). A malformed individual entry is
    skipped (with an error recorded) rather than failing the whole file,
    so one bad theme in a 600-entry pack doesn't take out the rest.
    """
    path = Path(path)
    themes: dict[str, dict] = {}
    errors: list[str] = []
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as e:
        return {}, [f"{path}: could not read/parse JSON ({e})"]

    if not isinstance(raw, list):
        return {}, [f"{path}: expected a JSON list of theme objects"]

    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            errors.append(f"{path}[{i}]: expected an object")
            continue
        entry_errors = _validate_terminal_entry(entry, f"{path}[{i}]")
        if entry_errors:
            errors.extend(entry_errors)
            continue
        themes[entry['name']] = terminal_theme_to_lazarus(entry)
    return themes, errors


def _apply_overrides(registry: dict[str, dict]) -> None:
    """Apply `settings.theme_overrides[name]` on top of matching REGISTRY
    entries, in place. Unknown theme names are logged and skipped (not a
    hard error -- a typo here shouldn't block startup)."""
    overrides = getattr(_settings, 'theme_overrides', None)
    if not overrides:
        return
    for name, keys in overrides.items():
        if name not in registry:
            logger.warning(
                "theme_overrides: no theme named %r in registry -- skipping", name)
            continue
        registry[name] = {**registry[name], **keys}


def build_registry() -> dict[str, dict]:
    """Assemble the full set of selectable themes:

    1. Hand-written Python themes (this module) -- always win on a name
       collision, since they're specifically tuned rather than heuristically
       mapped.
    2. The bundled JSON pack (`theme_packs/builtin.json`).
    3. User packs (`~/.config/lazarus/themes/*.json`) -- override same-named
       bundled entries, so a user can drop in a corrected copy of a theme.
    4. `settings.theme_overrides`, applied last, per-key, on top of whatever
       won above.

    Problems (malformed files/entries) are logged as warnings; they never
    prevent startup -- worst case, some themes are simply unavailable.
    """
    registry: dict[str, dict] = {
        'nord': nord,
        'solarized_dark': solarized_dark,
        'solarized_light': solarized_light,
        'catppuccin_macchiato': catppuccin_macchiato,
        'gruvbox_light': gruvbox_light,
        'gruvbox_light_hard': gruvbox_light_hard,
        'gruvbox_light_soft': gruvbox_light_soft,
        'gruvbox_dark': gruvbox_dark,
        'gruvbox_dark_hard': gruvbox_dark_hard,
        'gruvbox_dark_soft': gruvbox_dark_soft,
    }
    builtin_names = set(registry)

    builtin_pack = _builtin_pack_path()
    if builtin_pack.exists():
        mapped, errors = load_theme_pack(builtin_pack)
        for msg in errors:
            logger.warning("theme pack: %s", msg)
        for name, theme in mapped.items():
            if name in builtin_names:
                continue  # hand-tuned Python theme wins
            registry[name] = theme
    else:
        logger.warning("bundled theme pack not found at %s", builtin_pack)

    user_dir = _user_theme_dir()
    if user_dir.is_dir():
        for path in sorted(user_dir.glob('*.json')):
            mapped, errors = load_theme_pack(path)
            for msg in errors:
                logger.warning("theme pack: %s", msg)
            for name, theme in mapped.items():
                if name in builtin_names:
                    continue  # hand-tuned Python theme still wins
                registry[name] = theme

    _apply_overrides(registry)
    return registry


REGISTRY: dict[str, dict] = {}
"""Every selectable theme, by name: hand-written + bundled pack + user
packs + overrides. Populated by `build_registry()` -- call that once
config.py has run (so `settings.theme_overrides` is available), and again
if the user's theme directory changes."""


def apply_theme(theme: dict) -> None:
    """"Apply the given theme to GUI components

    This is called when :class:`~lazarus.app.Dodo` is initialised, and
    again on every live theme switch (`set_theme`)."""

    # Force the style to be the same on all OSs -- but only the first
    # time. Re-invoking QApplication.setStyle() on every live switch is
    # both pointless (the style name itself never changes, only the
    # palette/stylesheet do) and has been observed to be unstable once
    # QWebEngineView-bearing windows already exist.
    style = QApplication.style()
    if style is not None and style.objectName().lower() != "fusion":
        QApplication.setStyle("Fusion")
    # Now use a palette to switch to theme colors:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(theme['bg']))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(theme['fg']))
    palette.setColor(QPalette.ColorRole.Base, QColor(theme['bg']))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(theme['bg_alt']))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(theme['bg']))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(theme['fg']))
    palette.setColor(QPalette.ColorRole.Text, QColor(theme['fg']))
    palette.setColor(QPalette.ColorRole.Button, QColor(theme['bg_button']))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(theme['fg_button']))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(theme['fg_bright']))
    palette.setColor(QPalette.ColorRole.Link, QColor(theme['fg_link']))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(theme['bg_highlight']))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(theme['fg_highlight']))
    QApplication.setPalette(palette)
    # Application-wide stylesheet: thin scrollbar + rounded Fusion containers.
    # Derived from the same theme dict so every palette adapts automatically.
    # setStyleSheet is an *instance* method (unlike setStyle/setPalette which
    # are static), so call it on the live QApplication.
    inst = QApplication.instance()
    if isinstance(inst, QApplication):
        inst.setStyleSheet(build_global_stylesheet(theme))


# ---------------------------------------------------------------------------
# Live theme switching + persistence
# ---------------------------------------------------------------------------

_BUILTIN_ORDER = [
    'nord', 'solarized_dark', 'solarized_light', 'catppuccin_macchiato',
    'gruvbox_light', 'gruvbox_light_hard', 'gruvbox_light_soft',
    'gruvbox_dark', 'gruvbox_dark_hard', 'gruvbox_dark_soft',
]

_QSETTINGS_ORG = 'lazarus'
_QSETTINGS_APP = 'lazarus'
_QSETTINGS_KEY = 'last_theme_name'

_current_name: str | None = None
"""Name of the currently-applied theme, if it came from REGISTRY (i.e.
was set via `set_theme`). None if `settings.theme` was set some other
way (e.g. directly assigned a raw dict in config.py)."""


class ThemeError(Exception):
    """Raised by `set_theme` when asked for a name not in REGISTRY."""


def ordered_names(registry: dict[str, dict] | None = None) -> list[str]:
    """Stable cycling order: hand-written Python themes first (in the
    order above), then everything else alphabetically."""
    reg = REGISTRY if registry is None else registry
    hand_written = [n for n in _BUILTIN_ORDER if n in reg]
    rest = sorted(n for n in reg if n not in _BUILTIN_ORDER)
    return hand_written + rest


def current_name() -> str | None:
    return _current_name


def _find_name_for(theme: dict, registry: dict[str, dict]) -> str | None:
    """Best-effort reverse lookup: find a REGISTRY name whose dict *is*
    (identity, not equality -- cheap and avoids false positives between
    accidentally-identical themes) the given theme dict."""
    for name, t in registry.items():
        if t is theme:
            return name
    return None


def _save_last_theme_name(name: str) -> None:
    from PyQt6.QtCore import QSettings
    QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).setValue(_QSETTINGS_KEY, name)


def load_last_theme_name() -> str | None:
    from PyQt6.QtCore import QSettings
    val = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).value(_QSETTINGS_KEY)
    return val if isinstance(val, str) and val else None


def resolve_initial_theme() -> dict:
    """Called once at startup, after `build_registry()` has populated
    REGISTRY and config.py has run (so `settings.theme` reflects
    whatever the user's config.py set).

    Prefers a remembered theme name (from a previous live switch) over
    config.py's default, if that name still resolves in the current
    REGISTRY -- e.g. it survives a Lazarus upgrade unless the theme was
    actually removed/renamed.
    """
    global _current_name
    remembered = load_last_theme_name()
    if remembered is not None and remembered in REGISTRY:
        _current_name = remembered
        _settings.theme = REGISTRY[remembered]
        return _settings.theme

    # No usable remembered name -- fall back to whatever config.py set,
    # and try to identify its name (for cycling to start from the right
    # place) via identity lookup.
    _current_name = _find_name_for(_settings.theme, REGISTRY)
    return _settings.theme


def set_theme(theme: str | dict) -> dict:
    """Look up *theme* (by name in REGISTRY, or use directly if already
    a dict), apply it live, and persist the choice for next launch.

    Rebinds `settings.theme` to a *new* dict object rather than mutating
    the existing one in place -- `style.py`'s color/glyph caches are
    keyed on `id(settings.theme)` / the actual color values, so a
    rebind is what makes them self-invalidate without an explicit hook.

    :raises ThemeError: if *theme* is a string not found in REGISTRY.
    :returns: the resolved theme dict that was applied.
    """
    global _current_name
    if isinstance(theme, str):
        if theme not in REGISTRY:
            raise ThemeError(f"no such theme: {theme!r}")
        resolved = REGISTRY[theme]
        name: str | None = theme
    else:
        resolved = theme
        name = _find_name_for(theme, REGISTRY)

    _settings.theme = resolved
    apply_theme(resolved)
    _current_name = name
    if name is not None:
        _save_last_theme_name(name)
    return resolved

