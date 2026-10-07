"""Build the package's palette.json from a dump made by scripts/dump_palette.js.

    pbpaste | uv run scripts/build_palette.py

Boards store colours as RGB values of the legacy Atlassian palette. The
editor finds each value's palette name and paints it with that name's design
token in the current theme, so a stored #172B4D is drawn #292A2E. The table
maps every stored value to what the editor draws. Where two names share an
RGB value (a text colour and a dark border, say) both resolve to the same
token value; the first name in the editor's order is kept.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "confluence_whiteboard_exporter" / "palette.json"
OPAQUE = re.compile(r"#[0-9A-Fa-f]{6}")


def build(dump: dict) -> tuple[dict, list[str]]:
    colours: dict[str, dict] = {}
    problems = []
    for c in dump["colours"]:
        r, g, b = c["rgb"]
        stored = f"#{r:02X}{g:02X}{b:02X}"
        value = c["value"]
        if not value or not OPAQUE.fullmatch(value):
            continue  # shadows and other translucent tokens are never an element's colour
        value = value.upper()
        if stored in colours:
            if colours[stored]["value"] != value:
                problems.append(f"{stored}: {colours[stored]['name']} draws {colours[stored]['value']}, "
                                f"{c['name']} draws {value}; keeping the first")
            continue
        colours[stored] = {"value": value, "name": c["name"], "token": c["token"]}
    return {
        "source": "Confluence whiteboard editor palette resolved against its "
                  f"{dump['theme']} theme; regenerate with scripts/dump_palette.js and scripts/build_palette.py",
        "colours": colours,
    }, problems


def main() -> None:
    src = Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else sys.stdin.read()
    data, problems = build(json.loads(src))
    OUT.write_text(json.dumps(data, indent=1) + "\n")
    changed = sum(k != v["value"] for k, v in data["colours"].items())
    print(f"wrote {OUT} ({len(data['colours'])} colours, {changed} drawn differently from how they are stored)")
    for p in problems:
        print("  warning:", p)


if __name__ == "__main__":
    main()
