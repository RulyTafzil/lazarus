#!/usr/bin/env python3
"""Theme inspection, conversion, and compilation tool for Lazarus.

Inspect source terminal theme palettes with truecolor ANSI previews,
view how they map onto Lazarus's 19 semantic color keys, and compile
raw terminal packs into native, pre-computed Lazarus JSON themes.

Zero external dependencies: runs on any standard Python 3.9+ without a venv.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The complete set of 19 semantic keys Lazarus requires for themes
THEME_KEYS: tuple[str, ...] = (
    'bg', 'bg_alt', 'bg_button', 'bg_highlight',
    'fg', 'fg_bad', 'fg_bright', 'fg_button', 'fg_date', 'fg_dim',
    'fg_from', 'fg_good', 'fg_highlight', 'fg_link', 'fg_subject',
    'fg_subject_flagged', 'fg_subject_irrelevant', 'fg_subject_unread',
    'fg_tags',
)

# Standard ANSI terminal color names for indices 0-15
ANSI_COLOR_NAMES = {
    '0': 'black',
    '1': 'red',
    '2': 'green',
    '3': 'yellow',
    '4': 'blue',
    '5': 'magenta',
    '6': 'cyan',
    '7': 'white',
    '8': 'bright black',
    '9': 'bright red',
    '10': 'bright green',
    '11': 'bright yellow',
    '12': 'bright blue',
    '13': 'bright magenta',
    '14': 'bright cyan',
    '15': 'bright white',
}

# Default heuristic mapping terminal-theme entries onto Lazarus color keys
DEFAULT_TERMINAL_MAP: dict[str, tuple[tuple[str, str], ...]] = {
    'bg':                    (('named', 'background'),),
    'fg':                    (('named', 'foreground'),),
    'fg_dim':                (('palette', '8'), ('key', 'fg')),
    'fg_bright':             (('palette', '15'), ('palette', '7'), ('key', 'fg')),
    'fg_good':               (('palette', '10'), ('palette', '2'), ('key', 'fg')),
    'fg_bad':                (('palette', '9'), ('palette', '1'), ('key', 'fg')),
    'fg_link':               (('palette', '12'), ('palette', '4'),
                              ('palette', '14'), ('palette', '6'), ('key', 'fg')),
    'fg_button':             (('named', 'foreground'),),
    'bg_highlight':          (('named', 'selection-background'),
                              ('palette', '4'), ('key', 'fg')),
    'fg_highlight':          (('named', 'selection-foreground'), ('key', 'bg')),
    'fg_date':               (('key', 'fg_dim'),),
    'fg_from':               (('named', 'foreground'),),
    'fg_subject':            (('named', 'foreground'),),
    'fg_subject_unread':     (('palette', '14'), ('palette', '6'),
                              ('palette', '12'), ('palette', '4'), ('key', 'fg')),
    'fg_subject_irrelevant': (('key', 'fg_dim'),),
    'fg_subject_flagged':    (('palette', '11'), ('palette', '3'), ('key', 'fg')),
    'fg_tags':               (('palette', '12'), ('palette', '4'),
                              ('palette', '14'), ('palette', '6'), ('key', 'fg')),
}


def hex_to_rgb(hex_code: str) -> tuple[int, int, int]:
    h = hex_code.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"


def scale_hex(hex_code: str, factor: float) -> str:
    r, g, b = hex_to_rgb(hex_code)
    return rgb_to_hex(round(r * factor), round(g * factor), round(b * factor))


def hex_lightness(hex_code: str) -> int:
    r, g, b = hex_to_rgb(hex_code)
    return (max(r, g, b) + min(r, g, b)) // 2


def color_swatch(hex_code: str | None) -> str:
    """Return a 2-space terminal block colored with 24-bit truecolor ANSI."""
    if not hex_code or not hex_code.startswith('#') or len(hex_code) != 7:
        return '    '
    try:
        r, g, b = hex_to_rgb(hex_code)
        return f"\033[48;2;{r};{g};{b}m  \033[0m"
    except ValueError:
        return '    '


def resolve_chain(chain: tuple[tuple[str, str], ...],
                  entry: dict,
                  resolved: dict[str, str],
                  default: str) -> str:
    palette = entry.get('palette', {})
    for kind, value in chain:
        if kind == 'named':
            c = entry.get(value)
            if c:
                return c
        elif kind == 'palette':
            if value in palette:
                return palette[value]
        elif kind == 'key':
            if value in resolved:
                return resolved[value]
        elif kind == 'hex':
            return value
    return default


def terminal_theme_to_lazarus(entry: dict) -> dict[str, str]:
    """Map a raw terminal theme dict to Lazarus's 19 semantic color keys."""
    fg_default = entry.get('foreground', '#ffffff')
    theme: dict[str, str] = {}
    for key, chain in DEFAULT_TERMINAL_MAP.items():
        theme[key] = resolve_chain(chain, entry, theme, fg_default)

    bg = theme.get('bg', '#000000')
    is_dark = hex_lightness(bg) < 128
    theme['bg_alt'] = scale_hex(bg, 1.25) if is_dark else scale_hex(bg, 0.94)
    theme['bg_button'] = scale_hex(bg, 1.50) if is_dark else scale_hex(bg, 0.89)
    return theme


def load_raw_entries(raw_path: Path) -> dict[str, dict]:
    """Load raw terminal entries from JSON pack, keyed by name."""
    if not raw_path.exists():
        sys.exit(f"Error: Raw themes file not found at {raw_path}")
    data = json.loads(raw_path.read_text(encoding='utf-8'))
    if isinstance(data, list):
        return {entry['name']: entry for entry in data if isinstance(entry, dict) and 'name' in entry}
    elif isinstance(data, dict):
        return data
    sys.exit(f"Error: Unexpected format in {raw_path}")


def inspect_theme(name: str, entry: dict) -> None:
    """Print complete source palette and mapped Lazarus variables with color swatches."""
    mapped = terminal_theme_to_lazarus(entry)
    palette = entry.get('palette', {})

    print(f"\n\033[1m{'=' * 78}\033[0m")
    print(f"\033[1mTheme: {name}\033[0m")
    print(f"\033[1m{'=' * 78}\033[0m\n")

    print("\033[1m--- Source Terminal Palette (16 ANSI Colors) ---\033[0m")
    for i in range(8):
        c1_idx = str(i)
        c2_idx = str(i + 8)
        c1_hex = palette.get(c1_idx, '#000000')
        c2_hex = palette.get(c2_idx, '#000000')
        c1_name = ANSI_COLOR_NAMES.get(c1_idx, '')
        c2_name = ANSI_COLOR_NAMES.get(c2_idx, '')

        swatch1 = color_swatch(c1_hex)
        swatch2 = color_swatch(c2_hex)

        col1 = f"  {c1_idx:>2}: {swatch1} {c1_hex} ({c1_name})"
        col2 = f"  {c2_idx:>2}: {swatch2} {c2_hex} ({c2_name})"
        print(f"{col1:<38} {col2}")

    print("\n\033[1m--- Source Special Colors ---\033[0m")
    for key in ('background', 'foreground', 'cursor-color', 'selection-background', 'selection-foreground'):
        val = entry.get(key, '(not set)')
        swatch = color_swatch(val) if val.startswith('#') else '    '
        print(f"  {key:<24} {swatch} {val}")

    print("\n\033[1m--- Lazarus Mapped Semantic Variables (19 Keys) ---\033[0m")
    for lz_key in THEME_KEYS:
        hex_val = mapped.get(lz_key, '')
        swatch = color_swatch(hex_val)

        # Describe source origin
        origin = ""
        if lz_key in ('bg_alt', 'bg_button'):
            origin = "derived from bg"
        else:
            chain = DEFAULT_TERMINAL_MAP.get(lz_key, ())
            for kind, val in chain:
                if kind == 'named' and entry.get(val) == hex_val:
                    origin = f"from {val}"
                    break
                elif kind == 'palette' and palette.get(val) == hex_val:
                    origin = f"from palette {val} ({ANSI_COLOR_NAMES.get(val, '')})"
                    break
                elif kind == 'key' and mapped.get(val) == hex_val:
                    origin = f"from {val}"
                    break

        print(f"  {lz_key:<24} {swatch} {hex_val:<10} {('(' + origin + ')') if origin else ''}")

    print(f"\n\033[1m{'=' * 78}\033[0m\n")


def compile_pack(raw_path: Path, output_path: Path) -> None:
    """Compile all raw terminal entries into native Lazarus format."""
    entries = load_raw_entries(raw_path)
    compiled_list: list[dict] = []

    print(f"Compiling {len(entries)} themes from {raw_path.name}...")
    for name, entry in sorted(entries.items()):
        try:
            mapped = terminal_theme_to_lazarus(entry)
            item = {'name': name}
            item.update(mapped)
            compiled_list.append(item)
        except Exception as e:
            print(f"Warning: Failed to compile {name}: {e}", file=sys.stderr)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(compiled_list, indent=2), encoding='utf-8')
    size_kb = output_path.stat().st_size / 1024
    print(f"Successfully compiled {len(compiled_list)} themes to {output_path.name} ({size_kb:.1f} KB)")


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    default_raw = project_root / 'lazarus' / 'theme_packs' / 'raw_terminal_themes.json'
    default_out = project_root / 'lazarus' / 'theme_packs' / 'builtin.json'

    parser = argparse.ArgumentParser(
        description="Theme inspection and compilation tool for Lazarus email client."
    )
    parser.add_argument(
        '-i', '--inspect', metavar='NAME',
        help="Inspect all colors and mapping heuristics for a theme by name."
    )
    parser.add_argument(
        '-l', '--list', action='store_true',
        help="List all theme names available in the raw pack."
    )
    parser.add_argument(
        '-c', '--compile', action='store_true',
        help="Compile raw terminal themes into pre-resolved native Lazarus JSON."
    )
    parser.add_argument(
        '-e', '--export', metavar='NAME',
        help="Export a single theme as a native Lazarus JSON file."
    )
    parser.add_argument(
        '--raw', type=Path, default=default_raw,
        help=f"Path to raw terminal themes JSON (default: {default_raw.name})"
    )
    parser.add_argument(
        '--output', type=Path, default=default_out,
        help=f"Path to compiled output JSON (default: {default_out.name})"
    )

    args = parser.parse_args()

    if args.compile:
        compile_pack(args.raw, args.output)
        return

    raw_entries = load_raw_entries(args.raw)

    if args.list:
        print(f"Themes in {args.raw.name} ({len(raw_entries)} total):")
        for name in sorted(raw_entries.keys()):
            print(f"  {name}")
        return

    if args.inspect:
        target = args.inspect
        match = next((n for n in raw_entries if n.lower() == target.lower()), None)
        if not match:
            # Check for partial match
            partials = [n for n in raw_entries if target.lower() in n.lower()]
            if len(partials) == 1:
                match = partials[0]
            elif partials:
                names = ', '.join(partials[:8])
                sys.exit(f"Ambiguous name {target!r}. Did you mean: {names}?")
            else:
                sys.exit(f"Error: No theme named {target!r} found. Use --list to see all names.")
        inspect_theme(match, raw_entries[match])
        return

    if args.export:
        target = args.export
        match = next((n for n in raw_entries if n.lower() == target.lower()), None)
        if not match:
            sys.exit(f"Error: No theme named {target!r} found in {args.raw.name}.")
        mapped = terminal_theme_to_lazarus(raw_entries[match])
        out_dict = {'name': match, **mapped}
        out_file = args.output if args.output != default_out else Path(f"{match.lower().replace(' ', '_')}.json")
        out_file.write_text(json.dumps(out_dict, indent=2), encoding='utf-8')
        print(f"Exported {match} to {out_file}")
        return

    parser.print_help()


if __name__ == '__main__':
    main()
