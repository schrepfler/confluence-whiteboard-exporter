"""The editor's drawings (its Graphic objects) as data the renderers draw.

Shapes are drawn from these: the basic, flowchart and UML kinds from
shape_data.json, which the repo carries; the icon kinds and library icons
(Atlassian, AWS, Azure, Google Cloud) from what the extractor reads off the
live editor for a board's dump (graphics_probe.js), since their artwork is
not ours to publish.

A point is (x fraction, y fraction, x offset, y offset) and lands at
`box corner + fraction * box size + offset`, so offsets (rounded corners,
a cylinder's lid) keep their size whatever the box. A section is painted
with one of the element's colours (its fill, or its outline's colour), or,
in library icons, with a colour of its own.
"""

from __future__ import annotations

from typing import Any

Json = dict[str, Any]
PROBE_W, PROBE_H = 200, 300  # the box a definition's `boundingBoxToRenderBox` is probed with


def build(dump: dict[str, Json]) -> Json:
    """shape_data.json from a dump of every kind (scripts/dump_shapes.js)."""
    return {
        "source": "Confluence whiteboard editor shape definitions; regenerate with "
                  "scripts/dump_shapes.js and scripts/build_shape_data.py",
        "kinds": {kind: kind_entry(rec) for kind, rec in sorted(dump.items(), key=lambda kv: int(kv[0]))},
    }


def kind_entry(rec: Json) -> Json:
    """One shape kind, from its definition as the editor holds it."""
    out: Json = {"key": rec["key"], "category": rec["cat"], "renderer": rec["renderer"]}
    if rec.get("replicates") is not None:
        out["same_as"] = rec["replicates"]
    if rec.get("exterior"):
        out["exterior_text"] = rec["exterior"]
    if rec.get("noText"):
        out["no_text"] = True
    fit = rec.get("fit")
    if fit and (fit[2] - fit[0], fit[3] - fit[1]) != (PROBE_W, PROBE_H):
        # Drawn at a fixed aspect ratio, aligned to the top of the box.
        out["aspect"] = round((fit[2] - fit[0]) / (fit[3] - fit[1]), 4)
    if "fills" in rec:
        out["fills"] = [_section(s, rec["fills"]) for s in rec["fills"]]
        out["strokes"] = [_section(s, rec["fills"]) for s in rec["strokes"]]
        if rec.get("text"):
            out["text"] = rec["text"]
    elif rec["cat"] == "advanced":
        out["icon"] = True  # its artwork is read per board, not kept here
    return out


def icon_entry(rec: Json) -> Json:
    """A library icon: a drawing at its own aspect ratio, in its own colours."""
    w, h = rec["natural"]
    return {"natural": [w, h], "aspect": round(w / h, 4) if h else 1.0,
            "fills": [_section(s, rec["fills"]) for s in rec["fills"]],
            "strokes": [_section(s, rec["fills"]) for s in rec["strokes"]]}


def _section(sec: Json, fills: list[Json]) -> Json:
    segs = fills[sec["sameAsFill"]]["segs"] if "sameAsFill" in sec else sec["segs"]
    out: Json = {"path": _path(segs)}
    colour = sec.get("color")
    if colour and colour.get("rgba"):  # a colour of its own
        r, g, b, a = colour["rgba"]
        out["colour"] = f"#{int(r):02X}{int(g):02X}{int(b):02X}"
        if a < 255:
            out["opacity"] = round(a / 255, 3)
    elif colour:
        out["paint"] = colour["kind"].lower()  # painted with the element's other colour
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
