"""Shape kinds, drawn the way the whiteboard editor draws them.

The drawings in shape_data.json come from the editor's own shape
definitions (see scripts/build_shape_data.py). A drawing point is
(x fraction, y fraction, x offset, y offset) and lands at
`corner + fraction * size + offset`: outlines stretch with the box, while
offsets such as rounded corners or a cylinder's lid keep their size.

Icons (server, cloud, user, ...) are Atlassian artwork and are not shipped;
they are drawn as placeholders with their label below, as on the board.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

Box = tuple[float, float, float, float]
Cmd = list[Any]  # ["M", p] | ["L", p] | ["C", p, p, p] | ["Z"], p = [fx, fy, ox, oy]


def fmt(v: float | int) -> str:
    """Compact number formatting for SVG attributes."""
    if isinstance(v, int):
        return str(v)
    return f"{v:.1f}".rstrip("0").rstrip(".")


# The editor's shape enum, keyed as in its shape picker and i18n catalog.
KIND_NAMES: dict[int, str] = dict(enumerate([
    "sharp-rectangle", "rectangle", "ellipse", "rounded-rectangle", "diamond",
    "triangle", "upside-down-triangle", "left-parallelogram", "right-parallelogram",
    "start-end", "document", "off-page", "input-output", "database", "sum", "or",
    "predefined-process", "internal-storage", "manual-input", "manual-operation",
    "multiple-documents", "preparation", "hard-disk", "comment-left", "comment-right",
    "stored-data", "delay", "display", "process", "decision", "connector", "merge",
    "cloud", "key", "server", "archive", "browser", "user", "compute", "computer",
    "file", "firewall", "folder", "frontend", "internet", "lock", "mail", "mobile",
    "settings", "shield", "users", "switch", "database-advanced", "alternate-process",
    "use-case", "classifier", "note", "interface-2", "activation", "activity", "actor",
    "assembly", "component", "deletion", "end", "flow-final", "gateway",
    "history-pseudostate", "horizontal-fork", "vertical-fork", "off-page-link",
    "pin-filled-left", "pin-filled-right", "pin-left", "pin-right", "pin",
    "provided-interface", "receive-signal", "required-interface", "send-signal",
    "start", "template", "class", "interface", "node", "container",
    "boundary-object", "entity-object", "control-object",
]))
KIND_BY_NAME: dict[str, int] = {name: kind for kind, name in KIND_NAMES.items()}

# A shape with no kind is the model's default, kind 0.
DEFAULT_KIND = 0
SHARP_RECTANGLE = 0
PLACEHOLDER_KIND = 3  # icons are stood in for by a rounded rectangle

# Outlines as the editor strokes them: always 3 units wide, dashes repeating
# every 12 line widths. Where a dash pattern falls is described as a phase,
# in periods, along each segment; a dash shows where the phase is in a window.
SHAPE_LINE_WIDTH = 3.0
DASH_PERIOD = 12.0
DASH_SHARE = 0.6  # of a period that is dash, for lines and textured outlines
TEXTURE_WINDOW = (1 / 24, 1 / 24 + DASH_SHARE)  # the outline texture's dash, round ends included
CORNER_PHASE, CORNER_SPAN, EDGE_EXTRA = 0.09, 0.42, 0.58  # rounded polygons: see dash_layout
RUN_PHASE, RUN_DASH = 0.25, 0.5  # drawn outlines: dashes centred on each run's ends
_CORNER_COS = math.cos(math.radians(10))  # joins sharper than this are corners

_SHARP_PATH: list[Cmd] = [["M", [0, 0, 0, 0]], ["L", [1, 0, 0, 0]], ["L", [1, 1, 0, 0]],
                          ["L", [0, 1, 0, 0]], ["L", [0, 0, 0, 0]], ["Z"]]


@cache
def _kinds() -> dict[int, dict[str, Any]]:
    data = json.loads(Path(__file__).with_name("shape_data.json").read_text())
    kinds = {int(k): v for k, v in data["kinds"].items()}
    # The editor draws kind 0 as a plain rectangle; give it the same form.
    kinds[SHARP_RECTANGLE] = {**kinds.get(SHARP_RECTANGLE, {}), "fills": [{"path": _SHARP_PATH}],
                              "strokes": [{"path": _SHARP_PATH}]}
    return kinds


def resolve(kind: int | None, shape_map: dict[int, int] | None = None) -> int:
    """The kind whose drawing is used: after the caller's overrides and the
    editor's own reuse of one kind's drawing for another."""
    k = DEFAULT_KIND if kind is None else kind
    k = (shape_map or {}).get(k, k)
    seen = set()
    while (same := _kinds().get(k, {}).get("same_as")) is not None and k not in seen:
        seen.add(k)
        k = same
    return k


def is_icon(kind: int) -> bool:
    return bool(_kinds().get(kind, {}).get("icon"))


def can_draw(kind: int) -> bool:
    """Whether `kind` has a drawing of its own (or reuses one)."""
    return "fills" in _kinds().get(resolve(kind), {})


def kind_label(kind: int | None) -> str:
    k = DEFAULT_KIND if kind is None else kind
    return f"{KIND_NAMES.get(k, 'unknown')} ({k})"


Point = tuple[float, float]
Segment = tuple[Point, ...]  # (start, end) is a line; (start, c1, c2, end) a cubic


@dataclass(frozen=True)
class Subpath:
    segments: tuple[Segment, ...]
    closed: bool

    @property
    def d(self) -> str:
        if not self.segments:
            return ""
        parts = [f"M {_xy(self.segments[0][0])}"]
        for seg in self.segments:
            parts.append(f"L {_xy(seg[1])}" if len(seg) == 2 else "C " + " ".join(_xy(q) for q in seg[1:]))
        return " ".join(parts) + (" Z" if self.closed else "")


@dataclass(frozen=True)
class Section:
    paint: str  # "fill" or "stroke": how the section is drawn
    colour: str  # "fill" or "stroke": which of the shape's colours it uses
    subpaths: tuple[Subpath, ...]
    rule: str = "nonzero"
    dash_mode: str = "runs"  # how dashes are laid out: see dash_mode()

    @property
    def d(self) -> str:
        return " ".join(sub.d for sub in self.subpaths)


@dataclass(frozen=True)
class Drawing:
    kind: int  # the kind actually drawn
    sections: tuple[Section, ...]
    graphic: Box  # where the drawing sits
    text: Box | None  # where its text goes; None if the kind takes no text
    placeholder: bool = False  # no drawing available: an icon, or an unknown kind


def drawing(kind: int | None, x: float, y: float, w: float, h: float, shape_map: dict[int, int] | None = None) -> Drawing:
    """How to draw a shape of `kind` in the box (x, y, w, h)."""
    k = resolve(kind, shape_map)
    spec = _kinds().get(k)
    box = (x, y, w, h)
    if spec is None:  # a kind this version does not know
        return Drawing(k, sections(SHARP_RECTANGLE, *box), box, box, placeholder=True)

    graphic = box
    if aspect := spec.get("aspect"):  # fixed aspect ratio, aligned to the top
        graphic = (x, y, w, w / aspect)
    text: Box | None = graphic
    if spec.get("no_text"):
        text = None
    elif spec.get("exterior_text") == "+y" and "aspect" in spec:  # label below the graphic
        gh = graphic[3]
        text = (x, y + gh, w, max(h - gh, 24.0))
    elif spec.get("text"):
        (left, top), (right, bottom) = (_point(p, *graphic) for p in spec["text"])
        text = (left, top, right - left, bottom - top)

    if spec.get("icon"):
        return Drawing(k, sections(PLACEHOLDER_KIND, *graphic), graphic, text, placeholder=True)
    return Drawing(k, sections(k, *graphic), graphic, text)


def sections(kind: int, x: float, y: float, w: float, h: float) -> tuple[Section, ...]:
    spec = _kinds()[kind]
    mode = dash_mode(kind)
    out = []
    for paint in ("fill", "stroke"):
        for sec in spec.get(f"{paint}s", ()):
            other = "stroke" if paint == "fill" else "fill"
            colour = other if sec.get("paint") == other else paint
            subs = tuple(subpaths(sec["path"], x, y, w, h, mode))
            out.append(Section(paint, colour, subs, sec.get("rule", "nonzero"), mode))
    return tuple(out)


def dash_mode(kind: int) -> str:
    """How the editor lays dashes along a kind's outline.

    "even-left", "even-right": a whole number of periods evenly round the
    outline, clockwise from the top of the left side (rounded rectangle) or
    from the right (ellipse). "corners": each rounded corner is one dash and
    each edge carries whole periods (the other rounded polygons). "runs":
    whole periods between sharp corners, centred on dashes (drawn outlines).
    """
    renderer = _kinds().get(kind, {}).get("renderer")
    if renderer == "ellipse":
        return "even-right"
    if renderer == "roundedPolygon":
        return "even-left" if kind == 3 else "corners"
    return "runs"


def stretching_commands(kind: int) -> dict[str, Any] | None:
    """For the viewer: the raw commands of each section, in the order
    `sections` returns them, for a drawing that stretches with its box."""
    spec = _kinds().get(kind)
    if not spec or "fills" not in spec or "aspect" in spec:
        return None
    return {
        "dashMode": dash_mode(kind),
        "parts": [sec["path"] for paint in ("fill", "stroke") for sec in spec.get(f"{paint}s", ())],
    }


def path_data(cmds: list[Cmd], x: float, y: float, w: float, h: float) -> str:
    return " ".join(sub.d for sub in subpaths(cmds, x, y, w, h))


def subpaths(cmds: list[Cmd], x: float, y: float, w: float, h: float, mode: str = "runs") -> list[Subpath]:
    """Resolve commands to segments in the box. A closed outline is made to
    start, and run, where the editor's dash pattern does (see dash_mode)."""
    out: list[Subpath] = []
    segs: list[Segment] = []
    cur: Point | None = None

    def flush(closed: bool) -> None:
        if segs:
            out.append(Subpath(_reorder(segs, mode) if closed else tuple(segs), closed))
        segs.clear()

    for op, *pts in cmds:
        if op == "M":
            flush(False)
            cur = _point(pts[0], x, y, w, h)
        elif op == "Z":
            flush(True)
        else:
            assert cur is not None
            ends = [_point(q, x, y, w, h) for q in pts]
            segs.append((cur, *ends))
            cur = ends[-1]
    flush(False)
    return out


def dash_layout(sub: Subpath, width: float, mode: str) -> tuple[list[float], float]:
    """(stroke-dasharray, stroke-dashoffset) that reproduce the editor's
    dashes along `sub` with round caps; an empty array means solid.

    Each segment is given a dash phase (in periods) at its ends, varying
    linearly along it, and a dash shows where the phase falls in a window:
    - "even-*": the outline holds floor(length / period) periods evenly.
    - "corners": a corner spans phases 0.09-0.51, inside the dash; an edge
      of length L advances floor(L / period) + 0.58, so corners stay solid.
    - "runs": each run between sharp corners holds floor(L / period)
      periods, starting a quarter period in, so it starts and ends mid-dash;
      a run shorter than a period is solid.
    """
    period = DASH_PERIOD * width
    lengths = [_length(seg) for seg in sub.segments]
    total = sum(lengths)
    # (start, length, phase at start, phase at end, window) per segment
    spans: list[tuple[float, float, float, float, tuple[float, float] | None]] = []
    s = 0.0
    if mode.startswith("even"):
        n = int(total // period)
        if n == 0:
            return [], 0.0
        for length in lengths:
            spans.append((s, length, s * n / total, (s + length) * n / total, TEXTURE_WINDOW))
            s += length
    elif mode == "corners":
        phase = CORNER_PHASE if len(sub.segments[0]) == 4 else CORNER_PHASE + CORNER_SPAN
        for seg, length in zip(sub.segments, lengths, strict=True):
            step = CORNER_SPAN if len(seg) == 4 else (length // period) + EDGE_EXTRA
            spans.append((s, length, phase, phase + step, TEXTURE_WINDOW))
            s, phase = s + length, phase + step
    else:
        i = 0
        for run in _runs(sub):
            run_lengths = lengths[i:i + len(run)]
            i += len(run)
            run_total = sum(run_lengths)
            n = int(run_total // period)
            if n == 0:
                window = None  # solid
            else:
                cap = width / 2 / (run_total / n)
                window = (-cap, RUN_DASH + cap)
            phase = RUN_PHASE
            for length in run_lengths:
                step = length * n / run_total if run_total else 0.0
                spans.append((s, length, phase, phase + step, window))
                s, phase = s + length, phase + step

    on: list[list[float]] = []
    for start, length, p0, p1, window in spans:
        if length <= 0:
            continue
        pieces = [(start, start + length)] if window is None else _visible(start, length, p0, p1, window)
        for lo, hi in pieces:
            if on and lo - on[-1][1] < 1e-6:
                on[-1][1] = hi
            else:
                on.append([lo, hi])
    if not on or (on[0][0] < 1e-6 and on[-1][1] > total - 1e-6 and len(on) == 1):
        return [], 0.0
    # Round caps reach half a line width past each dash's core.
    cores = []
    for lo, hi in on:
        if hi - lo > width:
            cores.append((lo + width / 2, hi - width / 2))
        else:
            mid = (lo + hi) / 2
            cores.append((mid - 0.005, mid + 0.005))
    dashes: list[float] = []
    for k, (lo, hi) in enumerate(cores):
        dashes.append(hi - lo)
        dashes.append(cores[k + 1][0] - hi if k + 1 < len(cores) else total + width)
    return dashes, -cores[0][0]


def _visible(start: float, length: float, p0: float, p1: float, window: tuple[float, float]) -> list[tuple[float, float]]:
    """The stretches of a segment whose phase lies in the dash window."""
    a, b = window
    rate = (p1 - p0) / length
    out = []
    for k in range(math.floor(p0 - b), math.ceil(p1 - a) + 1):
        lo, hi = max(p0, k + a), min(p1, k + b)
        if hi > lo:
            out.append((start + (lo - p0) / rate, start + (hi - p0) / rate))
    return out


def _reorder(segs: list[Segment], mode: str) -> tuple[Segment, ...]:
    """Start a closed outline where the editor's dash pattern starts."""
    if mode.startswith("even"):
        if _signed_area(segs) < 0:  # make it clockwise on screen
            segs = [tuple(reversed(seg)) for seg in reversed(segs)]
        key = (lambda p: (p[0], p[1])) if mode == "even-left" else (lambda p: (-p[0], p[1]))
        first = min(range(len(segs)), key=lambda i: key(segs[i][0]))
        return tuple(segs[first:] + segs[:first])
    for i in range(len(segs)):  # from the first sharp corner, if any
        if _is_corner(segs[i - 1], segs[i]):
            return tuple(segs[i:] + segs[:i])
    return tuple(segs)


def _signed_area(segs: list[Segment]) -> float:
    pts = [seg[0] for seg in segs]
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1], strict=True))


def _runs(sub: Subpath) -> list[list[Segment]]:
    runs: list[list[Segment]] = []
    for seg in sub.segments:
        if runs and not _is_corner(runs[-1][-1], seg):
            runs[-1].append(seg)
        else:
            runs.append([seg])
    return runs


def _is_corner(a: Segment, b: Segment) -> bool:
    ta, tb = _unit(_end_tangent(a)), _unit(_start_tangent(b))
    return ta[0] * tb[0] + ta[1] * tb[1] < _CORNER_COS


def _start_tangent(seg: Segment) -> Point:
    for q in seg[1:]:
        if q != seg[0]:
            return q[0] - seg[0][0], q[1] - seg[0][1]
    return 0.0, 0.0


def _end_tangent(seg: Segment) -> Point:
    for q in reversed(seg[:-1]):
        if q != seg[-1]:
            return seg[-1][0] - q[0], seg[-1][1] - q[1]
    return 0.0, 0.0


def _unit(v: Point) -> Point:
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n else (0.0, 0.0)


def _length(seg: Segment, steps: int = 16) -> float:
    if len(seg) == 2:
        return math.dist(*seg)
    p0, c1, c2, p1 = seg
    total, prev = 0.0, p0
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        q = (u ** 3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * p1[0],
             u ** 3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * p1[1])
        total += math.dist(prev, q)
        prev = q
    return total


def _point(p: list[float], x: float, y: float, w: float, h: float) -> Point:
    fx, fy, ox, oy = p
    return x + fx * w + ox, y + fy * h + oy


def _xy(p: Point) -> str:
    return f"{fmt(p[0])} {fmt(p[1])}"
