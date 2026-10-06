"""Compare our export of the reference board with what the editor drew.

The reference (tests/reference/golden/geometry.json, written by
`wb2canvas reference snapshot`) holds the editor's drawn box for each
element and drawn path for each connector, per cell. Here the spec is
exported to SVG exactly as a user's board would be, and each element is
measured against it:

- a box by its largest edge difference;
- a path by the largest distance between the two lines (both sampled
  densely), so a route that bends differently shows however short it is.
  The editor's path runs to the end point under an arrowhead that covers
  the line; ours stops where such an arrowhead begins. The stretch an
  arrowhead covers is left out of the comparison.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .board import CAPS, from_dump, stable_ids
from .connectors import end_stub
from .model import ClipboardElement, DumpFile
from .reference import Cell, Json, payload, placements
from .svg import render_svg

Point = tuple[float, float]
STEP = 2.0  # sampling distance along paths, in board units


@dataclass
class Measure:
    place: int | str
    type: str
    what: str  # "box" or "path"
    difference: float  # board units
    ours: Json | None = None
    editor: Json | None = None


@dataclass
class CellResult:
    name: str
    measures: list[Measure] = field(default_factory=list)

    @property
    def worst(self) -> float:
        return max((m.difference for m in self.measures), default=0.0)


def compare(cells: list[Cell], golden: Json) -> list[CellResult]:
    elements = payload(cells)
    clip = [ClipboardElement.model_validate(e) for e in elements]
    ids = stable_ids(clip)
    board = from_dump(DumpFile.model_validate(
        {"board": {"boardId": "reference", "title": "reference", "spaceKey": "REF"},
         "strategy": "clipboard", "elements": clip}))
    svg = render_svg(board)
    boxes = {m["id"]: tuple(float(m[k]) for k in ("x", "y", "w", "h")) for m in _node_boxes(svg)}
    paths = dict(re.findall(r'<path class="wb-edge" data-id="([^"]+)"[^>]*?\sd="([^"]+)"', svg))
    by_place = {(name, place): i for i, (name, place) in enumerate(placements(cells))}

    results = []
    for cell in cells:
        result = CellResult(cell.name)
        for entry in golden["cells"].get(cell.name, []):
            i = by_place[(cell.name, entry["place"])]
            eid = ids[i]
            if "path" in entry and eid in paths:
                ours = svg_points(paths[eid])
                e = elements[i]
                theirs = _trim(editor_points(entry["path"]), _covered(e.get("startCap"), e.get("stroke", 1)),
                               _covered(e.get("endCap"), e.get("stroke", 1)))
                result.measures.append(Measure(entry["place"], entry["type"], "path", hausdorff(ours, theirs),
                                               {"d": paths[eid]}, entry["path"]))
            elif "box" in entry and eid in boxes and entry["type"] != "connector":
                x, y, w, h = boxes[eid]
                left, top, right, bottom = entry["box"]
                diff = max(abs(x - left), abs(y - top), abs(x + w - right), abs(y + h - bottom))
                result.measures.append(Measure(entry["place"], entry["type"], "box", diff,
                                               {"box": [x, y, x + w, y + h]}, {"box": entry["box"]}))
        results.append(result)
    return results


def _node_boxes(svg: str) -> list[dict[str, str]]:
    out = []
    for m in re.finditer(r'<g class="wb-node[^"]*"([^>]*)>', svg):
        attrs = dict(re.findall(r'data-([a-z]+)="([^"]*)"', m.group(1)))
        if all(k in attrs for k in ("id", "x", "y", "w", "h")):
            out.append(attrs)
    return out


# --------------------------------------------------------------------- paths


def editor_points(path: Json, step: float = STEP) -> list[Point]:
    """Sample a path in the editor's form: a start point, then segments
    relative to where the previous one ended. Lines carry an angle and a
    length; cubic Béziers their control points and end; arcs (the rounded
    bends of right-angled routes) a radius and the heading they turn from
    and to, turning right unless counterClockwise."""
    x, y = path["start"]
    pts: list[Point] = [(x, y)]
    for seg in path["segments"]:
        kind = seg["type"]
        if kind == "line":
            ex, ey = x + math.cos(seg["angle"]) * seg["length"], y + math.sin(seg["angle"]) * seg["length"]
            pts += _line(x, y, ex, ey, step)
            x, y = ex, ey
        elif kind == "cubic-bezier":
            c1, c2, end = seg["control1"], seg["control2"], seg["end"]
            p0, p1, p2, p3 = (x, y), (x + c1[0], y + c1[1]), (x + c2[0], y + c2[1]), (x + end[0], y + end[1])
            pts += _cubic(p0, p1, p2, p3, step)
            x, y = p3
        elif kind == "arc":
            r, h0, h1 = seg["radius"], seg["startAngle"], seg["endAngle"]
            side = -1.0 if seg.get("counterClockwise") else 1.0  # +1: centre to the right of travel
            cx, cy = x - side * r * math.sin(h0), y + side * r * math.cos(h0)
            n = max(2, math.ceil(abs(h1 - h0) * r / step))
            for k in range(1, n + 1):
                h = h0 + (h1 - h0) * k / n
                pts.append((cx + side * r * math.sin(h), cy - side * r * math.cos(h)))
            x, y = pts[-1]
    return pts


_TOKEN = re.compile(r"[MLC]|-?[0-9.]+(?:e-?[0-9]+)?")


def svg_points(d: str, step: float = STEP) -> list[Point]:
    """Sample an SVG path made of M, L and C commands (all ours uses)."""
    tokens = _TOKEN.findall(d)
    pts: list[Point] = []
    cur: Point = (0.0, 0.0)
    i = 0
    while i < len(tokens):
        cmd = tokens[i]
        nums = []
        i += 1
        while i < len(tokens) and tokens[i] not in "MLC":
            nums.append(float(tokens[i]))
            i += 1
        if cmd == "M":
            cur = (nums[0], nums[1])
            pts.append(cur)
        elif cmd == "L":
            for k in range(0, len(nums), 2):
                pts += _line(*cur, nums[k], nums[k + 1], step)
                cur = (nums[k], nums[k + 1])
        elif cmd == "C":
            for k in range(0, len(nums), 6):
                c1, c2, end = (nums[k], nums[k + 1]), (nums[k + 2], nums[k + 3]), (nums[k + 4], nums[k + 5])
                pts += _cubic(cur, c1, c2, end, step)
                cur = end
    return pts


def _covered(cap: int | None, stroke: int) -> float:
    """How much of the line an arrowhead hides (and our path leaves out)."""
    length, hidden = end_stub(CAPS.get(cap or 1, "none"), stroke)
    return length if hidden else 0.0


def _trim(points: list[Point], start: float, end: float) -> list[Point]:
    """Drop the points within `start` of the first and `end` of the last,
    measured along the line."""
    def cut(pts: list[Point], length: float) -> list[Point]:
        if length <= 0:
            return pts
        walked = 0.0
        for k in range(1, len(pts)):
            walked += math.dist(pts[k - 1], pts[k])
            if walked >= length - 1e-6:
                return pts[k:]
        return pts[-1:]
    return cut(cut(points, start)[::-1], end)[::-1]


def hausdorff(a: list[Point], b: list[Point]) -> float:
    """The largest distance from a point of either line to the other."""
    def one_way(p: list[Point], q: list[Point]) -> float:
        return max(min(math.dist(u, v) for v in q) for u in p)
    if not a or not b:
        return math.inf
    return max(one_way(a, b), one_way(b, a))


def _line(x0: float, y0: float, x1: float, y1: float, step: float) -> list[Point]:
    n = max(1, math.ceil(math.dist((x0, y0), (x1, y1)) / step))
    return [(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n) for k in range(1, n + 1)]


def _cubic(p0: Point, p1: Point, p2: Point, p3: Point, step: float) -> list[Point]:
    length = math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)
    n = max(2, math.ceil(length / step))
    out = []
    for k in range(1, n + 1):
        t = k / n
        u = 1 - t
        out.append((u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                    u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]))
    return out
