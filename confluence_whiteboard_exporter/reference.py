"""The reference board: every element the export draws, in its variations,
generated from code and pasted into a whiteboard (see docs/plan.md).

The board is a grid of cells. Each cell holds one variation and a caption
naming it ("connector/curved/end filled-arrow"). Pasting moves everything
by one offset, so a cell is found again by its caption: in an export, and
on the canvas.

Elements are built in the editor's clipboard format, the same one the
extractor reads from a copy: positions are centres, vectors carry their
"Vector2"/"Vector3" type, text is an ADF document, and connectors, labels
and waypoints name what they belong to by index in the payload.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from .board import CAPS, ROUTINGS, STROKE_STYLES
from .shapes import KIND_NAMES, text_area

Json = dict[str, Any]

CELL_W, CELL_H = 480.0, 420.0  # one variation per cell, with room for what grows
COLUMNS = 10
CAPTION_Y = -CELL_H / 2 + 24  # captions sit along the top of their cell

# Colours as boards store them (the legacy palette; see palette.py).
TEXT = (23, 43, 77)  # textDefaultColor
LINE = (117, 129, 149)  # n600, the default connector colour
FILL = (204, 224, 255)  # b200
FILLS = {  # a sample of the palette, by name
    "n400": (179, 185, 196), "b300": (133, 184, 255), "o300": (254, 193, 118),
    "m300": (247, 151, 210), "t300": (116, 219, 231), "g300": (118, 224, 180),
    "white": (255, 255, 255), "n200": (241, 242, 244),
}
STROKES = {"b800": (0, 85, 204), "g800": (33, 110, 78), "o800": (151, 79, 12), "p800": (94, 77, 178)}
STICKY = (255, 222, 184)  # orange, a new sticky's colour
STICKIES = {  # the editor's classic sticky colours
    "orange": STICKY, "b200": (204, 224, 255), "p200": (223, 216, 253), "m200": (253, 208, 236),
    "y150": (255, 239, 174), "g150": (199, 248, 227), "teal": (200, 244, 249), "red": (255, 210, 204),
}
SECTION = (200, 244, 249)  # teal, a new section's colour
SECTION_FILLS = {  # from each palette group: light, medium, strong; and white
    "b100": (233, 242, 255), "y100": (255, 247, 214), "r100": (255, 237, 235),
    "teal": SECTION, "b200": (204, 224, 255), "y150": (255, 239, 174), "orange": STICKY, "n200": (241, 242, 244),
    "b300": (133, 184, 255), "y300": (255, 222, 92), "n400": (179, 185, 196), "white": (255, 255, 255),
}
ROUTING_IDS = {name: pr for pr, name in ROUTINGS.items()}
CAP_IDS = {name: i for i, name in CAPS.items()}
STYLE_IDS = {name: i for i, name in STROKE_STYLES.items()}
SIDE_IDS = {"centre": 0, "left": 1, "right": 2}


def v2(x: float, y: float) -> Json:
    return {"x": float(x), "y": float(y), "type": "Vector2"}


def v3(rgb: tuple[int, int, int]) -> Json:
    r, g, b = rgb
    return {"x": float(r), "y": float(g), "z": float(b), "type": "Vector3"}


def doc(*blocks: Json) -> str:
    return json.dumps({"version": 1, "type": "doc", "content": list(blocks)})


def para(text: str, *, bold: bool = False) -> Json:
    run: Json = {"type": "text", "text": text}
    if bold:
        run["marks"] = [{"type": "strong"}]
    return {"type": "paragraph", "content": [run]}


def heading(text: str, level: int) -> Json:
    return {"type": "heading", "attrs": {"level": level}, "content": [{"type": "text", "text": text}]}


def bullets(*items: str) -> Json:
    return {"type": "bulletList", "content": [{"type": "listItem", "content": [para(t)]} for t in items]}


# ------------------------------------------------------------ element builders
# Positions are relative to the cell's centre; indices are within the cell.


def shape(kind: int, x: float, y: float, w: float, h: float, text: str | None = None, **opts: Any) -> Json:
    content = opts.pop("content", None)
    return {
        "type": "shape", "source": 1, "position": v2(x, y), "size": v2(w, h),
        "color": v3(opts.pop("fill", FILL)), "strokeColor": v3(opts.pop("stroke", TEXT)),
        "strokeStyle": opts.pop("style", 1), "text": content or (doc(para(text)) if text else doc()),
        "shape": kind, "fillEnabled": opts.pop("filled", True), "fontScale": opts.pop("scale", 1.0),
        "basisSize": v2(w, h), "basisPosition": v2(x, y), "alignment": opts.pop("align", "center"),
        "verticalAlignment": opts.pop("valign", 1), "rotation": 0.0, **opts,
    }


def sticky(x: float, y: float, words: str | None = None, w: float = 144, h: float = 144, **opts: Any) -> Json:
    content = opts.pop("content", None)
    return {
        "type": "sticky", "source": 1, "position": v2(x, y), "size": v2(w, h), "color": v3(opts.pop("color", STICKY)),
        "text": content or (doc(para(words)) if words else doc()), "fontScale": opts.pop("scale", 1.0),
        "basisSize": v2(w, h), "basisPosition": v2(x, y), "alignment": opts.pop("align", "center"),
        "verticalAlignment": opts.pop("valign", 1), "rotation": 0.0, **opts,
    }


def sticker(x: float, y: float, sprite: str, size: float = 85, rotation: float = 0.0) -> Json:
    """A sticker from one of the editor's packs (85 square by default)."""
    return {"type": "sticker", "source": 1, "position": v2(x, y), "size": v2(size, size), "spriteId": sprite,
            "rotation": rotation}


def stamp(x: float, y: float, sprite: str, size: float = 60, rotation: float = 0.0,
          on: tuple[int, Json] | None = None) -> Json:
    """A stamp (60 square by default), optionally put `on` an element: its
    index in the cell, and that element's top-left corner, which the stamp's
    centre is kept relative to."""
    out = {"type": "stamp", "source": 1, "position": v2(x, y), "size": v2(size, size), "rotation": rotation,
           "spriteId": sprite}
    if on is not None:
        index, parent = on
        left = parent["position"]["x"] - parent["size"]["x"] / 2
        top = parent["position"]["y"] - parent["size"]["y"] / 2
        out["attachedTo"] = {"parentIndex": index, "offset": [x - left, y - top]}
    return out


def section(x: float, y: float, w: float, h: float, title: str, **opts: Any) -> Json:
    return {"type": "section", "source": 1, "position": v2(x, y), "size": v2(w, h),
            "color": v3(opts.pop("color", SECTION)), "rotation": 0.0, "title": title, "titleWidth": 140,
            "hasDropShadow": opts.pop("shadow", False), **opts}


def text(x: float, y: float, content: str, *, align: str = "left", width: float | None = None,
         scale: float = 1.0, color: tuple[int, int, int] = TEXT) -> Json:
    """Free text. With no width it is flexible and grows to fit its text."""
    w = width or 34.0
    return {
        "type": "text", "source": 1, "position": v2(x, y), "size": v2(w, 38), "color": v3(color),
        "text": content, "fontScale": scale, "basisSize": v2(w, 38), "basisPosition": v2(x, y),
        "alignment": align, "rotation": 0.0, "allowFlexibleWidth": width is None,
    }


def connector(source: int | None, target: int | None, *, routing: str = "curved", start: str = "none",
              end: str = "arrow", stroke: int = 1, style: str = "solid", color: tuple[int, int, int] = LINE,
              source_anchor: tuple[float, float] = (1, 0.5), target_anchor: tuple[float, float] = (0, 0.5),
              start_point: tuple[float, float] = (0, 0), end_point: tuple[float, float] = (0, 0)) -> Json:
    """A connector between two elements of the cell; None leaves that end
    loose, at `start_point` / `end_point`."""
    def anchor(a: tuple[float, float]) -> Json:
        return {"left": float(a[0]), "top": float(a[1])}
    return {
        "type": "connector", "source": 1, "position": v2(0, 0), "size": v2(0, 0), "color": v3(color),
        "strokeStyle": STYLE_IDS[style],
        "sourceElement": None if source is None else f"cell-{source}",
        "sourceAnchor": None if source is None else anchor(source_anchor),
        "sourceIndex": -1 if source is None else source,
        "targetElement": None if target is None else f"cell-{target}",
        "targetAnchor": None if target is None else anchor(target_anchor),
        "targetIndex": -1 if target is None else target,
        "startCap": CAP_IDS[start], "endCap": CAP_IDS[end], "presentation": ROUTING_IDS[routing],
        "stroke": stroke, "start": list(start_point), "end": list(end_point), "segments": [],
    }


def waypoint(path: int, x: float, y: float, order: float, axis: int = 2) -> Json:
    """A bend of the connector at index `path`. Right-angled connectors pin
    a vertical (axis 0) or horizontal (axis 1) segment; others use 2."""
    return {"type": "pathWaypoint", "source": 1, "position": v2(x, y), "size": v2(1, 1),
            "sourcePathIndex": path, "sourcePathId": f"cell-{path}", "order": order, "axis": axis}


def label(path: int, words: str, proportion: float = 0.5, side: str = "centre") -> Json:
    return {"type": "pathLabel", "source": 1, "position": v2(0, 0), "size": v2(0, 0), "color": v3(TEXT),
            "text": doc(para(words)), "fontScale": 1.0, "sourcePathIndex": path, "sourcePathId": f"cell-{path}",
            "proportion": proportion, "pathOffsetPosition": SIDE_IDS[side]}


def icon(x: float, y: float, icon_id: str, category: str, collection: str = "aws") -> Json:
    return {"type": "advanced-icon", "source": 1, "position": v2(x, y), "size": v2(160, 160), "color": v3(TEXT),
            "text": doc(), "fontScale": 1.0, "basisSize": v2(108, 1), "basisPosition": v2(x, y),
            "alignment": "center", "verticalAlignment": 1, "iconId": icon_id, "category": category,
            "collection": collection}


def line(x0: float, y0: float, x1: float, y1: float, *, style: str = "solid", stroke: int = 1) -> Json:
    return {"type": "path", "source": 1, "position": v2(0, 0), "size": v2(0, 0), "color": v3(LINE),
            "strokeStyle": STYLE_IDS[style], "startCap": 1, "endCap": 1, "presentation": 1, "stroke": stroke,
            "start": [x0, y0], "end": [x1, y1],
            "segments": [{"type": "line", "angle": math.atan2(y1 - y0, x1 - x0), "length": math.hypot(x1 - x0, y1 - y0)}]}


# ------------------------------------------------------------------- the spec


@dataclass
class Cell:
    name: str
    elements: list[Json] = field(default_factory=list)


def _pair(edge: Json, *, gap: float = 300, size: tuple[float, float] = (90, 60), dy: float = 0,
          extra: tuple[Json, ...] | list[Json] = ()) -> list[Json]:
    """Two small boxes and an edge between them (indices 0, 1, 2)."""
    w, h = size
    return [shape(1, -gap / 2, -dy / 2, w, h, fill=FILLS["n200"]),
            shape(1, gap / 2, dy / 2, w, h, fill=FILLS["n200"]), edge, *extra]


def spec() -> list[Cell]:
    cells: list[Cell] = []
    add = lambda name, *els: cells.append(Cell(name, list(els)))  # noqa: E731

    # Every shape kind, with its name as text. Drawings with a label below
    # are drawn at their own aspect ratio, so they get a narrower box.
    for kind, name in KIND_NAMES.items():
        area = text_area(kind, 100)
        if area is not None and area.label_below:
            add(f"shape/{kind} {name}", shape(kind, 0, -30, 100, 100, name))
        else:
            add(f"shape/{kind} {name}", shape(kind, 0, 20, 200, 120, name))
    for kind in (1, 2, 3, 4, 13):
        add(f"shape/{kind} wide", shape(kind, 0, 20, 360, 90, "wide"))
        add(f"shape/{kind} tall", shape(kind, 0, 20, 120, 220, "tall"))

    # Outlines and fills.
    for style in ("solid", "dashed", "dotted", "none"):
        for filled in (True, False):
            add(f"outline/{style} {'filled' if filled else 'unfilled'}",
                shape(3, 0, 20, 220, 120, style, style=STYLE_IDS[style], filled=filled))
    for name, rgb in FILLS.items():
        add(f"colour/fill {name}", shape(3, 0, 20, 220, 120, name, fill=rgb))
    for name, rgb in STROKES.items():
        add(f"colour/outline {name}", shape(3, 0, 20, 220, 120, name, stroke=rgb, filled=False))

    # Text in shapes.
    for valign, vname in ((0, "top"), (1, "middle"), (2, "bottom")):
        for align in ("left", "center", "right"):
            add(f"text/{align} {vname}", shape(1, 0, 20, 260, 180, f"{align} {vname}", align=align, valign=valign))
    for scale in (0.5, 1.5, 2.0):
        add(f"text/scale {scale:g}", shape(1, 0, 20, 260, 180, f"scale {scale:g}", scale=scale))
    add("text/headings", shape(1, 0, 20, 300, 260, content=doc(heading("Heading 1", 1), heading("Heading 2", 2),
                                                              heading("Heading 3", 3), para("Body"))))
    add("text/list", shape(1, 0, 20, 280, 200, content=doc(para("Title", bold=True),
                                                           bullets("first item", "second item", "third"))))
    long = "Text too long for its box, so the box grows to fit it"
    for kind in (1, 2, 3, 4):
        add(f"text/overflow {KIND_NAMES[kind]}", shape(kind, 0, -60, 240, 60, long))
    add("text/empty", shape(3, 0, 20, 220, 120))

    # Free text.
    for align in ("left", "center", "right"):
        add(f"free text/{align} flexible", text(0, 0, doc(para(f"{align} aligned free text")), align=align))
        add(f"free text/{align} fixed width", text(0, 0, doc(para(f"{align} aligned text in a fixed width box")),
                                                   align=align, width=200))
    add("free text/scale 2", text(0, 0, doc(para("scaled")), scale=2.0))
    add("free text/list", text(0, 0, doc(bullets("one", "two", "three"))))

    # Connectors.
    for routing in ("straight", "curved", "dynamic"):
        add(f"connector/{routing}", *_pair(connector(0, 1, routing=routing), dy=120))
    for end in CAPS.values():
        add(f"connector/end {end}", *_pair(connector(0, 1, routing="straight", end=end)))
        add(f"connector/both ends {end}", *_pair(connector(0, 1, routing="straight", start=end, end=end)))
    for stroke in (1, 2, 3):
        for style in ("solid", "dashed", "dotted"):
            add(f"connector/size {stroke} {style}", *_pair(connector(0, 1, routing="curved", stroke=stroke,
                                                                     style=style), dy=80))
    add("connector/curved through waypoints", *_pair(connector(0, 1, routing="curved"),
                                                     extra=[waypoint(2, -50, -90, 1), waypoint(2, 60, 80, 2)]))
    add("connector/straight through a waypoint", *_pair(connector(0, 1, routing="straight"),
                                                        extra=[waypoint(2, 0, -100, 1)]))
    add("connector/right-angled handle x", *_pair(connector(0, 1, routing="dynamic"), dy=140,
                                                  extra=[waypoint(2, 40, 0, 1, axis=0)]))
    add("connector/right-angled handle y", *_pair(connector(0, 1, routing="dynamic"), dy=140,
                                                  extra=[waypoint(2, 0, 100, 1, axis=1)]))
    add("connector/right-angled two handles", *_pair(connector(0, 1, routing="dynamic"), dy=140,
                                                     extra=[waypoint(2, 0, -110, 1, axis=1),
                                                            waypoint(2, 60, 0, 2, axis=0)]))
    for routing, sa, ta, dy in (("dynamic", (0.5, 1), (0.5, 0), 160), ("dynamic", (1, 0.5), (1, 0.5), 160),
                                ("curved", (0.5, 0), (0.5, 0), 0), ("curved", (0.5, 1), (0, 0.5), 140)):
        add(f"connector/{routing} sides {sa}->{ta}", *_pair(connector(0, 1, routing=routing, source_anchor=sa,
                                                                      target_anchor=ta), dy=dy))
    for side in ("centre", "left", "right"):
        for proportion in (0.25, 0.5):
            add(f"label/{side} at {proportion:g}", *_pair(connector(0, 1, routing="curved"), dy=80,
                                                          extra=[label(2, side, proportion, side)]))
    add("label/two on one connector", *_pair(connector(0, 1, routing="straight"),
                                             extra=[label(2, "first", 0.3), label(2, "second", 0.7, "left")]))
    add("label/on a right-angled connector", *_pair(connector(0, 1, routing="dynamic"), dy=140,
                                                    extra=[label(2, "bend", 0.5)]))
    add("connector/loose end", shape(1, -150, 0, 90, 60, fill=FILLS["n200"]),
        connector(0, None, routing="curved", end_point=(150, -60)))
    add("connector/loose start", shape(1, 150, 0, 90, 60, fill=FILLS["n200"]),
        connector(None, 0, routing="straight", start_point=(-150, 60), source_anchor=(0, 0.5),
                  target_anchor=(0, 0.5)))

    # Other elements.
    add("icon/aws", icon(0, 0, "Amazon-Simple-Storage-Service", "storage"))
    add("line/solid", line(-180, 0, 180, 0))
    add("line/dashed", line(-180, 40, 180, -40, style="dashed", stroke=2))

    # Stickies.
    for name, rgb in STICKIES.items():
        add(f"sticky/colour {name}", sticky(0, 20, name, color=rgb))
    wraps = "This sticky has a lot more text than fits on one line, so it grows"
    add("sticky/wraps", sticky(0, 0, wraps))
    add("sticky/overflows", sticky(0, -40, " ".join([wraps] * 2)))
    add("sticky/empty", sticky(0, 20))
    add("sticky/left top", sticky(0, 20, "left top", align="left", valign=0))
    add("sticky/right bottom", sticky(0, 20, "right bottom", align="right", valign=2))
    add("sticky/wide", sticky(0, 20, "wide", w=300))
    add("sticky/tall", sticky(0, 20, "tall", h=260))
    add("sticky/small", sticky(0, 20, "small", w=100, h=100))
    add("sticky/scale 2", sticky(0, -40, "Scaled", scale=2.0))  # grows to 288, from its top
    add("sticky/scale 0.5", sticky(0, 20, "Scaled down", scale=0.5))
    add("sticky/heading and list", sticky(0, 0, content=doc(heading("Title", 3), bullets("one", "two"))))

    # Sections: what lies on them is drawn over them, their title on a tab.
    for name, rgb in SECTION_FILLS.items():
        add(f"section/colour {name}", section(0, 30, 300, 200, name, color=rgb))
    add("section/long title", section(0, 30, 220, 160, "A section with a much longer title than its box"))
    add("section/no title", section(0, 30, 300, 200, ""))
    add("section/drop shadow", section(0, 30, 300, 200, "Shadow", shadow=True))
    add("section/small", section(0, 30, 120, 90, "Small"))
    add("section/with elements", section(0, 30, 380, 260, "Contents"), sticky(-90, 30, "On it"),
        shape(1, 100, 30, 120, 80, "Shape"))
    add("section/pasted last", sticky(-60, 30, "Under?"), section(0, 30, 320, 240, "Pasted last"))

    # Stickers, from several packs (their images are read per board).
    for sprite in ("AWS-Lambda", "ai_platform", "AtlassianStickers-blank-page", "facilitators-thumbs-up",
                   "Race-Car", "Squiggle-1", "open-goodjob"):
        add(f"sticker/{sprite}", sticker(0, 20, sprite))
    add("sticker/large", sticker(0, 20, "facilitators-thumbs-up", size=300))
    add("sticker/turned", sticker(0, 20, "Race-Car", size=160, rotation=0.6))

    # Stamps, on their own or put on an element (their SVGs are read per board).
    for sprite in ("thumbs-up", "fire", "100", "heart-eyes", "idea", "warning"):
        add(f"stamp/{sprite}", stamp(0, 20, sprite))
    add("stamp/large", stamp(0, 20, "gold-star", size=160))
    add("stamp/turned", stamp(0, 20, "rocket", size=100, rotation=0.5))
    note = sticky(0, 30, "Stamped")
    add("stamp/on a sticky", note, stamp(60, -30, "thumbs-up", on=(0, note)))
    box = shape(1, 0, 30, 200, 100, "Approved")
    add("stamp/on a shape", box, stamp(90, -10, "success", on=(0, box)), stamp(-90, 70, "plus-one", on=(0, box)))
    return cells


# ---------------------------------------------------------------- the payload


def payload(cells: list[Cell]) -> list[Json]:
    """The cells laid out in a grid, as one clipboard payload: positions
    moved to each cell's place, indices moved to the payload's."""
    out: list[Json] = []
    for n, cell in enumerate(cells):
        cx, cy = (n % COLUMNS) * CELL_W, (n // COLUMNS) * CELL_H
        base = len(out)
        for e in cell.elements:
            out.append(_placed(e, cx, cy, base))
        out.append(text(cx - CELL_W / 2 + 24, cy + CAPTION_Y, doc(para(cell.name)), color=LINE))
    return out


def _placed(e: Json, dx: float, dy: float, base: int) -> Json:
    e = json.loads(json.dumps(e))
    for key in ("position", "basisPosition"):
        if key in e and e["type"] not in ("connector", "path", "pathLabel"):
            e[key]["x"] += dx
            e[key]["y"] += dy
    for key in ("start", "end"):
        if key in e:
            e[key] = [e[key][0] + dx, e[key][1] + dy]
    for key in ("sourceIndex", "targetIndex", "sourcePathIndex"):
        if key in e and e[key] >= 0:
            e[key] += base
    if e.get("attachedTo"):
        e["attachedTo"]["parentIndex"] += base
    for key in ("sourceElement", "targetElement", "sourcePathId"):
        if e.get(key):
            e[key] = f"ref-{int(e[key].split('-')[1]) + base}"
    return e


def cell_rects(cells: list[Cell]) -> dict[str, tuple[float, float, float, float]]:
    """Each cell's rectangle (x, y, w, h) in the payload's coordinates."""
    return {cell.name: ((n % COLUMNS) * CELL_W - CELL_W / 2, (n // COLUMNS) * CELL_H - CELL_H / 2, CELL_W, CELL_H)
            for n, cell in enumerate(cells)}


def cell_slug(name: str) -> str:
    """A file name for a cell: "connector/end arrow" -> "connector-end-arrow"."""
    return re.sub(r"[^a-z0-9.]+", "-", name.lower()).strip("-")


def shows_artwork(cell: Cell) -> bool:
    """Whether the editor draws artwork in the cell that is not ours to
    publish (icon shapes, library icons, stickers, stamps): its reference
    image is kept outside the repo."""
    from .shapes import is_icon

    return any((e["type"] == "shape" and is_icon(e["shape"])) or e["type"] in ("advanced-icon", "sticker", "stamp")
               for e in cell.elements)


def placements(cells: list[Cell]) -> list[tuple[str, int | str]]:
    """For each payload element, its cell and its place in it: the index in
    the cell's elements, or "caption"."""
    out: list[tuple[str, int | str]] = []
    for cell in cells:
        out += [(cell.name, i) for i in range(len(cell.elements))] + [(cell.name, "caption")]
    return out


# ------------------------------------------------------------- the reference
# What the editor drew for each element, keyed by cell and place, in the
# payload's coordinates (the board's, less the offset the paste moved
# everything by), so rebuilding the board does not change it.


def match_board(cells: list[Cell], board: dict[str, Json], centres: dict[str, tuple[float, float]],
                geometry: Json) -> Json:
    """The editor's drawn geometry per cell.

    `board` is the board's stored elements by id (the Yjs form: `t` type,
    `se`/`te` connector ends, `pi` a label's connector, `pp` its proportion,
    `pop` its side), `centres` their stored centres, `geometry` what
    geometry_probe.js read: drawn `boxes` and `paths` by id.
    """
    elements = payload(cells)
    where = placements(cells)
    offset = paste_offset(elements, board, centres)
    found: dict[int, str] = {}
    by_type: dict[str, list[str]] = {}
    for eid, el in board.items():
        by_type.setdefault(el.get("t"), []).append(eid)

    def near(eid: str, x: float, y: float) -> bool:
        cx, cy = centres.get(eid, (math.inf, math.inf))
        return abs(cx - x - offset[0]) < 0.05 and abs(cy - y - offset[1]) < 0.05

    for i, e in enumerate(elements):
        if e["type"] in ("shape", "text", "advanced-icon", "sticky", "section", "sticker", "stamp"):
            hits = [eid for eid in by_type.get(e["type"], []) if near(eid, e["position"]["x"], e["position"]["y"])]
            if len(hits) == 1:
                found[i] = hits[0]
    for i, e in enumerate(elements):
        if e["type"] == "connector":
            ends = (found.get(e["sourceIndex"]), found.get(e["targetIndex"]))
            hits = [eid for eid in by_type.get("connector", [])
                    if (board[eid].get("se"), board[eid].get("te")) == ends]
            if len(hits) == 1:
                found[i] = hits[0]
    for i, e in enumerate(elements):
        if e["type"] == "pathLabel" and (path := found.get(e["sourcePathIndex"])):
            hits = [eid for eid in by_type.get("pathLabel", []) if board[eid].get("pi") == path
                    and abs(board[eid].get("pp", -1) - e["proportion"]) < 1e-6
                    and board[eid].get("pop", 0) == e["pathOffsetPosition"]]
            if len(hits) == 1:
                found[i] = hits[0]
        elif e["type"] == "path":
            hits = [eid for eid, p in geometry["paths"].items() if board.get(eid, {}).get("t") == "path"
                    and abs(p["start"][0] - e["start"][0] - offset[0]) < 0.5
                    and abs(p["start"][1] - e["start"][1] - offset[1]) < 0.5]
            if len(hits) == 1:
                found[i] = hits[0]

    ox, oy = offset
    cells_out: dict[str, list[Json]] = {cell.name: [] for cell in cells}
    for i, e in enumerate(elements):
        if e["type"] == "pathWaypoint":
            continue  # drawn as part of its connector
        name, place = where[i]
        entry: Json = {"place": place, "type": e["type"]}
        eid = found.get(i)
        if eid is None:
            entry["missing"] = True
        else:
            if (box := geometry["boxes"].get(eid)) is not None:
                entry["box"] = [round(box[0] - ox, 2), round(box[1] - oy, 2), round(box[2] - ox, 2), round(box[3] - oy, 2)]
            if (path := geometry["paths"].get(eid)) is not None:
                entry["path"] = {"start": [round(path["start"][0] - ox, 2), round(path["start"][1] - oy, 2)],
                                 "end": [round(path["end"][0] - ox, 2), round(path["end"][1] - oy, 2)],
                                 "segments": _rounded(path["segments"])}
        cells_out[name].append(entry)
    return {"spec_hash": spec_hash(elements), "cells": cells_out}


def paste_offset(elements: list[Json], board: dict[str, Json],
                  centres: dict[str, tuple[float, float]]) -> tuple[float, float]:
    """How far the paste moved everything: the most common difference
    between where a text element was put and where the board has it."""
    from collections import Counter

    texts = [e for e in elements if e["type"] == "text"]
    stored = [centres[eid] for eid, el in board.items() if el.get("t") == "text" and eid in centres]
    votes: Counter[tuple[float, float]] = Counter()
    first = texts[0]["position"]
    for x, y in stored:
        votes[(round(x - first["x"], 1), round(y - first["y"], 1))] += 1
    # Every text element of the spec agrees on the true offset.
    best = max(votes, key=lambda o: sum(any(abs(x - e["position"]["x"] - o[0]) < 0.05 and
                                            abs(y - e["position"]["y"] - o[1]) < 0.05 for x, y in stored)
                                        for e in texts[:20]))
    return best


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, list):
        return [_rounded(v) for v in value]
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    return value


def clipboard_html(elements: list[Json]) -> str:
    """What the editor writes to, and reads from, the clipboard."""
    data = base64.b64encode(json.dumps(elements).encode("utf-8")).decode("ascii")
    return f'<div id="canvas-clipboard" data-canvas-clipboard="{data}"></div>'


def spec_hash(elements: list[Json]) -> str:
    return hashlib.sha256(json.dumps(elements, sort_keys=True).encode()).hexdigest()[:16]
