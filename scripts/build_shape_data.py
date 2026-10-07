"""Build the package's shape_data.json from a dump made by scripts/dump_shapes.js.

    pbpaste | uv run scripts/build_shape_data.py

Each drawing is kept in the editor's own coordinates: a point is
(x fraction, y fraction, x offset, y offset) and lands at
`box corner + fraction * box size + offset`, so offsets (rounded corners,
a cylinder's lid) keep their size whatever the box. Icon artwork is not in
the dump; icons keep only what is needed to lay out their label.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "confluence_whiteboard_exporter" / "shape_data.json"
PROBE_W, PROBE_H = 200, 300  # the box dump_shapes.js probes `boundingBoxToRenderBox` with


def build(dump: dict[str, dict]) -> dict:
    kinds = {}
    for kind, rec in sorted(dump.items(), key=lambda kv: int(kv[0])):
        out: dict = {"key": rec["key"], "category": rec["cat"], "renderer": rec["renderer"]}
        if rec["replicates"] is not None:
            out["same_as"] = rec["replicates"]
        if rec["exterior"]:
            out["exterior_text"] = rec["exterior"]
        if rec["noText"]:
            out["no_text"] = True
        fit = rec["fit"]
        if fit and (fit[2] - fit[0], fit[3] - fit[1]) != (PROBE_W, PROBE_H):
            # Drawn at a fixed aspect ratio, aligned to the top of the box.
            out["aspect"] = round((fit[2] - fit[0]) / (fit[3] - fit[1]), 4)
        if "fills" in rec:
            out["fills"] = [_section(s, rec["fills"]) for s in rec["fills"]]
            out["strokes"] = [_section(s, rec["fills"]) for s in rec["strokes"]]
            if rec.get("text"):
                out["text"] = rec["text"]
        elif rec["cat"] == "advanced":
            out["icon"] = True
        kinds[kind] = out
    return {
        "source": "Confluence whiteboard editor shape definitions; regenerate with "
                  "scripts/dump_shapes.js and scripts/build_shape_data.py",
        "kinds": kinds,
    }


def _section(sec: dict, fills: list[dict]) -> dict:
    if "sameAsFill" in sec:
        segs = fills[sec["sameAsFill"]]["segs"]
    else:
        segs = sec["segs"]
    out: dict = {"path": _path(segs)}
    if sec.get("color"):
        out["paint"] = sec["color"]["kind"].lower()  # painted with the other colour
    if sec.get("rule") == "evenodd":
        out["rule"] = "evenodd"
    return out


def _path(segs: list) -> list:
    """Segments, which each repeat their start point, as path commands."""
    cmds: list = []
    current = start = None
    for seg in segs:
        op, first = seg[0], seg[1]
        if op == "M" or first != current:
            if current is not None and current == start and len(cmds) > 1:
                cmds.append(["Z"])
            cmds.append(["M", first])
            current = start = first
        if op == "L":
            cmds.append(["L", seg[2]])
            current = seg[2]
        elif op == "C":
            cmds.append(["C", seg[2], seg[3], seg[4]])
            current = seg[4]
    if current is not None and current == start and len(cmds) > 1:
        cmds.append(["Z"])
    return cmds


def main() -> None:
    src = Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else sys.stdin.read()
    data = build(json.loads(src))
    OUT.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    drawn = sum("fills" in k for k in data["kinds"].values())
    print(f"wrote {OUT} ({len(data['kinds'])} kinds, {drawn} drawings)")


if __name__ == "__main__":
    main()
