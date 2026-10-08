"""Build the package's shape_data.json from a dump made by scripts/dump_shapes.js.

    pbpaste | uv run scripts/build_shape_data.py

Each drawing is kept in the editor's own coordinates: a point is
(x fraction, y fraction, x offset, y offset) and lands at
`box corner + fraction * box size + offset`, so offsets (rounded corners,
a cylinder's lid) keep their size whatever the box. Icon artwork is not in
the dump; icons keep only what is needed to lay out their label (the
extractor reads their artwork per board: see drawings.py).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from confluence_whiteboard_exporter.drawings import _path, build  # noqa: F401 - _path: for the tests

OUT = Path(__file__).resolve().parent.parent / "confluence_whiteboard_exporter" / "shape_data.json"


def main() -> None:
    src = Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else sys.stdin.read()
    data = build(json.loads(src))
    OUT.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    drawn = sum("fills" in k for k in data["kinds"].values())
    print(f"wrote {OUT} ({len(data['kinds'])} kinds, {drawn} drawings)")


if __name__ == "__main__":
    main()
