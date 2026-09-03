#!/usr/bin/env python3
"""Theme inspection, conversion, and compilation tool for Lazarus.

Inspect source terminal theme palettes with truecolor ANSI previews,
view how they map onto Lazarus's 19 semantic color keys, and compile
raw terminal packs into native, pre-computed Lazarus JSON themes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to sys.path so lazarus modules can be imported
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lazarus import themes


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


def color_swatch(hex_code: str | None) -> str:
    """Return a 2-space terminal block colored with 24-bit truecolor ANSI."""
    if not hex_code or not hex_code.startswith('#') or len(hex_code) != 7:
        return '    '
    try:
        r = int(hex_code[1:3], 16)
        g = int(hex_code[3:5], 16)
        b = int(hex_code[5:7], 16)
        return f"\033[48;2;{r};{g};{b}m  \033[0m"
    except ValueError:
        return '    '


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
    mapped = themes.terminal_theme_to_lazarus(entry)
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
    for lz_key in themes.THEME_KEYS:
        hex_val = mapped.get(lz_key, '')
        swatch = color_swatch(hex_val)

        # Describe source origin
        origin = ""
        if lz_key in ('bg_alt', 'bg_button'):
            origin = "derived from bg"
        else:
            chain = themes.DEFAULT_TERMINAL_MAP.get(lz_key, ())
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

    print(f"Compiling {len(entries)} themes from {raw_path}...")
    for name, entry in sorted(entries.items()):
        try:
            mapped = themes.terminal_theme_to_lazarus(entry)
            item = {'name': name}
            item.update(mapped)
            compiled_list.append(item)
        except Exception as e:
            print(f"Warning: Failed to compile {name}: {e}", file=sys.stderr)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(compiled_list, indent=2), encoding='utf-8')
    size_kb = output_path.stat().st_size / 1024
    print(f"Successfully compiled {len(compiled_list)} themes to {output_path} ({size_kb:.1f} KB)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Theme inspection and compilation tool for Lazarus email client."
    )
    default_raw = PROJECT_ROOT / 'lazarus' / 'theme_packs' / 'raw_terminal_themes.json'
    default_out = PROJECT_ROOT / 'lazarus' / 'theme_packs' / 'builtin.json'

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
        # Case-insensitive lookup
        match = next((n for n in raw_entries if n.lower() == target.lower()), None)
        if not match:
            sys.exit(f"Error: No theme named {target!r} found in {args.raw.name}. Use --list to see all names.")
        inspect_theme(match, raw_entries[match])
        return

    if args.export:
        target = args.export
        match = next((n for n in raw_entries if n.lower() == target.lower()), None)
        if not match:
            sys.exit(f"Error: No theme named {target!r} found in {args.raw.name}.")
        mapped = themes.terminal_theme_to_lazarus(raw_entries[match])
        out_dict = {'name': match, **mapped}
        out_file = args.output if args.output != default_out else Path(f"{match.lower().replace(' ', '_')}.json")
        out_file.write_text(json.dumps(out_dict, indent=2), encoding='utf-8')
        print(f"Exported {match} to {out_file}")
        return

    parser.print_help()


if __name__ == '__main__':
    main()
