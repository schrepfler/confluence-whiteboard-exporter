"""Connector geometry, following the whiteboard editor's own router.

Renderer-independent: `route()` returns the connector's path as line and
cubic segments in board units, and `ARROWHEADS` describes each line end.
See docs/confluence-whiteboard-model.md for where the numbers come from.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]

# The editor's spline "broadness": 0.38 since its toolbar refresh (0.3 before).
TENSION = 0.38
# Line thickness and arrowhead scale per stroke size (1 small, 2 medium, 3 large).
THICKNESS = {1: 2.0, 2: 4.0, 3: 6.0}
ARROWHEAD_SCALE = {1: 1.0, 2: 1.2, 3: 1.6}
ELBOW_STUB = 24.0  # how far a right-angled connector runs straight out of its box
BEND_RADIUS = 10.0  # right-angled connectors round their bends


def thickness(stroke_size: int) -> float:
    return THICKNESS.get(stroke_size, THICKNESS[1])


# ---------------------------------------------------------------- arrowheads


@dataclass(frozen=True)
class Arrowhead:
    """One line end, as the editor defines it.

    The graphic is drawn in a unit box: x runs 0..1 toward the line's end
    and y -0.5..0.5 across it, scaled by `size`. Its centre sits
    `offset` before the end. Where `line_visible` is false, the last
    2 * offset of the line is hidden under the graphic.
    """

    size: Point
    offset: float
    line_visible: bool
    filled: bool
    lines: tuple[tuple[Point, ...], ...] = ()  # open polylines
    polygons: tuple[tuple[Point, ...], ...] = ()  # closed outlines
    ellipses: tuple[tuple[float, float, float, float], ...] = ()  # cx, cy, rx, ry

    def stub(self, stroke_size: int) -> float:
        """Length of line the arrowhead occupies at the end."""
        return 2 * self.offset * ARROWHEAD_SCALE.get(stroke_size, 1.0)


_TRIANGLE = ((0.0, 0.5), (1.0, 0.0), (0.0, -0.5))
_DIAMOND = ((0.0, 0.0), (0.5, -0.5), (1.0, 0.0), (0.5, 0.5))
_FOOT = (((1.0, -0.5), (0.0, 0.0), (1.0, 0.5)), ((0.0, 0.0), (1.0, 0.0)))

ARROWHEADS: dict[str, Arrowhead] = {
    "arrow": Arrowhead((10, 14), 0, True, False, lines=(((-0.5, 0.5), (0.5, 0.0), (-0.5, -0.5)),)),
    "filled-arrow": Arrowhead((8, 12), 4, False, True, polygons=(_TRIANGLE,)),
    "open-arrow": Arrowhead((11, 15), 5, False, False, polygons=(_TRIANGLE,)),
    "filled-diamond": Arrowhead((16, 12), 8, False, True, polygons=(_DIAMOND,)),
    "open-diamond": Arrowhead((17, 13), 8, False, False, polygons=(_DIAMOND,)),
    "open-circle": Arrowhead((9, 9), 4, False, False, ellipses=((0.5, 0.0, 0.5, 0.5),)),
    "slash": Arrowhead((8, 12), 4, True, False, lines=(((0.0, 0.5), (1.0, -0.5)),)),
    "triple-bar": Arrowhead((14, 14), 6, False, False, lines=(
        ((0.0, -0.5), (0.0, 0.5)), ((0.5, -0.5), (0.5, 0.5)), ((1.0, -0.5), (1.0, 0.5)),
        ((0.0, 0.0), (1.0, 0.0)))),
    "open-circle-cross": Arrowhead((14, 14), 10, False, False,
                                   lines=(((1.0, -0.5), (1.0, 0.5)), ((0.619, 0.0), (1.275, 0.0))),
                                   ellipses=((0.19, 0.0, 0.429, 0.429),)),
    "cross": Arrowhead((12, 12), 6, True, False, lines=(((0.5, -0.5), (0.5, 0.5)),)),
    "cross-crows-foot": Arrowhead((12, 12), 6, False, False, lines=(((0.0, -0.5), (0.0, 0.5)), *_FOOT)),
    "crows-foot": Arrowhead((12, 12), 6, False, False, lines=_FOOT),
    "circle-crows-foot": Arrowhead((24, 12), 12, False, False,
                                   lines=(((1.0, -0.5), (0.5, 0.0), (1.0, 0.5)), ((0.5, 0.0), (1.0, 0.0))),
                                   ellipses=((0.25, 0.0, 0.25, 0.5),)),
}


def end_stub(cap: str, stroke_size: int) -> tuple[float, bool]:
    """(length the arrowhead occupies, whether that part of the line is hidden)."""
    head = ARROWHEADS.get(cap)
    if head is None or head.offset == 0:
        return 0.0, False
    return head.stub(stroke_size), not head.line_visible


# -------------------------------------------------------------------- routes


@dataclass(frozen=True)
class Route:
    start: Point
    segments: tuple[tuple[Point, ...], ...]  # (end,) is a line; (c1, c2, end) a cubic

    def points(self) -> list[Point]:
        """Every point and control point; a cubic lies inside their hull."""
        return [self.start, *(p for seg in self.segments for p in seg)]


@dataclass(frozen=True)
class End:
    point: Point
    side: str | None  # box edge the end is anchored to; None if free or centred
    cap: str = "none"
    box: tuple[float, float, float, float] | None = None  # (x, y, w, h) of the element it is attached to


def route(
    routing: str,
    start: End,
    end: End,
    waypoints: tuple[Point, ...] = (),
    stroke_size: int = 1,
    axes: tuple[str | None, ...] = (),
) -> Route:
    """`axes` gives each waypoint's handle axis on a right-angled connector:
    "x" pins a vertical segment at the waypoint's x, "y" a horizontal one."""
    if routing == "straight":
        return _straight(start, end, waypoints, stroke_size)
    if routing == "dynamic":
        return _dynamic(start, end, waypoints, axes, stroke_size)
    return _curved(start, end, waypoints, stroke_size)


def _straight(start: End, end: End, waypoints: tuple[Point, ...], stroke_size: int) -> Route:
    pts = [start.point, *waypoints, end.point]
    pts = _trim_ends(pts, start.cap, end.cap, stroke_size)
    return Route(pts[0], tuple((p,) for p in pts[1:]))


def _dynamic(start: End, end: End, waypoints: tuple[Point, ...], axes: tuple[str | None, ...], stroke_size: int) -> Route:
    """Right angles, with rounded bends. Through waypoints this is the
    editor's own route; without them the editor routes around other shapes,
    while this takes a simple elbow."""
    if waypoints and len(axes) == len(waypoints) and all(a in ("x", "y") for a in axes):
        pts = _through_handles(start, end, waypoints, axes, stroke_size)  # type: ignore[arg-type]
    else:
        s_ext, _ = end_stub(start.cap, stroke_size)
        t_ext, _ = end_stub(end.cap, stroke_size)
        stub = max(ELBOW_STUB, s_ext + 10, t_ext + 10)
        pts = elbow_points(start.point, end.point, start.side, end.side, stub)
    pts = _trim_ends(_drop_collinear(pts), start.cap, end.cap, stroke_size)
    return _rounded(pts, BEND_RADIUS)


_SIDE_DIR = {"right": "+x", "left": "-x", "bottom": "+y", "top": "-y"}
_OPPOSITE = {"+x": "-x", "-x": "+x", "+y": "-y", "-y": "+y"}


def _through_handles(start: End, end: End, waypoints: tuple[Point, ...], axes: tuple[str, ...], stroke_size: int) -> list[Point]:
    """The editor's route through segment handles (computeFindOrthogonal-
    PathWithWaypoints). The path is a list of coordinates that alternately
    move x and y: the start, a stub out to the source box's margin, each
    handle's pinned coordinate (gaps between two handles on the same axis
    are interpolated), a stub in from the target's margin, and the end."""
    margin = 4 * thickness(stroke_size) + 10

    def edge(box: tuple[float, float, float, float], direction: str) -> float:
        x, y, w, h = box
        return {"+x": x + w + margin, "-x": x - margin, "+y": y + h + margin, "-y": y - margin}[direction]

    s_dir = _SIDE_DIR.get(start.side or "") if start.box else None
    t_dir = _OPPOSITE[_SIDE_DIR[end.side]] if end.side in _SIDE_DIR and end.box else None  # heading in
    x_first = s_dir[1] == "x" if s_dir else axes[0] == "x"
    vals: list[float | None] = list(start.point if x_first else start.point[::-1])

    def misfit(moves_x: bool) -> bool:  # the next value would move the other axis
        return moves_x != ((len(vals) % 2 == 0) == x_first)

    if s_dir and start.box and misfit(axes[0] == "x"):
        vals.append(edge(start.box, s_dir))
    for p, axis in zip(waypoints, axes, strict=True):
        if misfit(axis == "x"):
            vals.append(None)
        vals.append(p[0] if axis == "x" else p[1])
    x_last = not (t_dir[1] == "x" if t_dir else axes[-1] == "x")
    if misfit(x_last):
        if t_dir and end.box:
            vals.append(edge(end.box, _OPPOSITE[t_dir]))
        else:
            x_last = not x_last
    vals += list(end.point if x_last else end.point[::-1])
    _fill_gaps(vals, 0)
    _fill_gaps(vals, 1)
    pts = [start.point]
    for i in range(2, len(vals)):
        prev, cur = vals[i - 1], vals[i]
        pts.append((cur, prev) if ((i % 2 == 0) == x_first) else (prev, cur))  # type: ignore[arg-type]
    return [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]


def _fill_gaps(vals: list[float | None], parity: int) -> None:
    """Interpolate missing values between known ones of the same parity,
    as the editor does (including its quirk of keeping the last value from
    before a gap as the reference)."""
    run, last = 0, 0.0
    for i in range(parity, len(vals), 2):
        v = vals[i]
        if v is None:
            run += 1
        elif run:
            for n in range(run):
                vals[i - 2 * n - 2] = v + (last - v) * (n + 1) / (run + 1)
            run = 0
        else:
            last = v


def _rounded(pts: list[Point], radius: float) -> Route:
    """A polyline with each bend rounded by a quarter circle of `radius`."""
    k = 0.5523  # cubic handle length for a quarter circle, per unit radius
    segs: list[tuple[Point, ...]] = []
    for i in range(1, len(pts) - 1):
        a, p, b = pts[i - 1], pts[i], pts[i + 1]
        din, dout = _unit(_sub(p, a)), _unit(_sub(b, p))
        if abs(din[0] * dout[0] + din[1] * dout[1]) > 0.999:  # straight on, or doubling back
            segs.append((p,))
            continue
        r = min(radius, _dist(a, p) / 2, _dist(p, b) / 2)
        p_in, p_out = _sub(p, _scale(din, r)), _add(p, _scale(dout, r))
        segs.append((p_in,))
        segs.append((_add(p_in, _scale(din, r * k)), _sub(p_out, _scale(dout, r * k)), p_out))
    segs.append((pts[-1],))
    return Route(pts[0], tuple(segs))


def _curved(start: End, end: End, waypoints: tuple[Point, ...], stroke_size: int) -> Route:
    """The editor's curve: a smooth spline through the waypoints that leaves
    and enters perpendicular to the anchored box edges, plus a straight stub
    under any arrowhead that needs room."""
    pts = [start.point, *waypoints, end.point]
    s_ext, s_hidden = end_stub(start.cap, stroke_size)
    t_ext, t_hidden = end_stub(end.cap, stroke_size)
    a0 = _end_angle(start.side, pts, at_end=False, extended=bool(s_ext or t_ext))
    a1 = _end_angle(end.side, pts, at_end=True, extended=bool(s_ext or t_ext))

    spline_pts = list(pts)
    lead = s_ext > 0 and a0 is not None
    tail = t_ext > 0 and a1 is not None
    if lead:
        spline_pts[0] = _add(pts[0], _polar(a0, s_ext))  # type: ignore[arg-type]
    if tail:
        spline_pts[-1] = _add(pts[-1], _polar(a1 + math.pi, t_ext))  # type: ignore[operator]

    segments: list[tuple[Point, ...]] = []
    first = pts[0]
    if lead and not s_hidden:
        segments.append((spline_pts[0],))
    elif lead:
        first = spline_pts[0]
    segments.extend(smooth_spline(spline_pts, a0, a1))
    if tail and not t_hidden:
        segments.append((pts[-1],))
    return Route(first, tuple(segments))


def point_at(path: Route, proportion: float) -> tuple[Point, float]:
    """The point `proportion` (0-1) of the way along `path`, by length, and
    the path's direction there (an angle)."""
    pieces: list[tuple[Point, ...]] = []
    cur = path.start
    for seg in path.segments:
        pieces.append((cur, *seg))
        cur = seg[-1]
    lengths = [_seg_length(p) for p in pieces]
    remaining = max(0.0, min(1.0, proportion)) * sum(lengths)
    for i, (piece, length) in enumerate(zip(pieces, lengths, strict=True)):
        if remaining > length and i < len(pieces) - 1:
            remaining -= length
            continue
        t = _t_at_length(piece, remaining) if length else 0.0
        return _eval(piece, t), _angle_at(piece, t)
    return path.start, 0.0


def _seg_length(piece: tuple[Point, ...], steps: int = 32) -> float:
    if len(piece) == 2:
        return _dist(*piece)
    total, prev = 0.0, piece[0]
    for i in range(1, steps + 1):
        q = _eval(piece, i / steps)
        total, prev = total + _dist(prev, q), q
    return total


def _t_at_length(piece: tuple[Point, ...], length: float, steps: int = 32) -> float:
    if len(piece) == 2:
        full = _dist(*piece)
        return length / full if full else 0.0
    walked, prev = 0.0, piece[0]
    for i in range(1, steps + 1):
        q = _eval(piece, i / steps)
        step = _dist(prev, q)
        if walked + step >= length:
            return (i - 1 + ((length - walked) / step if step else 0.0)) / steps
        walked, prev = walked + step, q
    return 1.0


def _eval(piece: tuple[Point, ...], t: float) -> Point:
    if len(piece) == 2:
        (x0, y0), (x1, y1) = piece
        return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
    p0, c1, c2, p1 = piece
    u = 1 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * p1[0],
            u ** 3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * p1[1])


def _angle_at(piece: tuple[Point, ...], t: float) -> float:
    if len(piece) == 2:
        return math.atan2(piece[1][1] - piece[0][1], piece[1][0] - piece[0][0])
    return _bezier_angle(*piece, t)  # type: ignore[call-arg]


def smooth_spline(
    pts: list[Point], start_angle: float | None, end_angle: float | None, tension: float = TENSION
) -> list[tuple[Point, Point, Point]]:
    """Cubic segments through `pts` ("smooth series" mode).

    Each interior point's tangent bisects the directions to its neighbours,
    with handles `tension` times the neighbouring distance; the end handles
    follow the given angles, or else point at the nearest handle.
    """
    n = len(pts)
    if n < 2:
        return []
    p0, pn = pts[0], pts[-1]
    if n == 2 and start_angle is None and end_angle is None:
        return [(p0, pn, pn)]  # a straight segment
    ct = _add(p0, _scale(_polar(start_angle), _dist(p0, pts[1]) * tension)) if start_angle is not None else None
    lt = _sub(pn, _scale(_polar(end_angle), _dist(pn, pts[-2]) * tension)) if end_angle is not None else None

    handles: list[Point] = []
    for i in range(1, n - 1):
        prev = ct if (i == 1 and ct) else pts[i - 1]
        nxt = lt if (i == n - 2 and lt) else pts[i + 1]
        t = _unit(_add(_unit(_sub(pts[i], prev)), _unit(_sub(nxt, pts[i]))))
        handles.append(_sub(pts[i], _scale(t, _dist(pts[i - 1], pts[i]) * tension)))
        handles.append(_add(pts[i], _scale(t, _dist(pts[i], pts[i + 1]) * tension)))

    def toward(p: Point, h: Point) -> Point:
        return _add(p, _scale(_unit(_sub(h, p)), _dist(p, h) * tension))

    if ct is None and lt is not None:
        handles.append(lt)
        handles.insert(0, toward(p0, handles[0]))
    else:
        handles.insert(0, ct if ct is not None else toward(p0, handles[0]))
        handles.append(lt if lt is not None else toward(pn, handles[-1]))
    return [(handles[2 * i], handles[2 * i + 1], pts[i + 1]) for i in range(n - 1)]


def _end_angle(side: str | None, pts: list[Point], *, at_end: bool, extended: bool) -> float | None:
    """Direction the curve leaves its start (or arrives at its end) in."""
    if side is not None:
        angle = _SIDE_ANGLE[side]
        return angle + math.pi if at_end else angle  # arrive heading into the box
    if len(pts) > 2:
        if not extended:
            return None
        three = pts[-3:] if at_end else pts[:3]
        c1, c2, e = smooth_spline(three, None, None)[-1 if at_end else 0]
        s = three[1] if at_end else three[0]
        return _bezier_angle(s, c1, c2, e, 1.0 if at_end else 0.0)
    (x0, y0), (x1, y1) = pts
    w = math.atan2(y1 - y0, x1 - x0)
    return w + 0.68 * math.sin((w - math.pi / 2) / 0.5)


_SIDE_ANGLE = {"right": 0.0, "bottom": math.pi / 2, "left": math.pi, "top": -math.pi / 2}


def _bezier_angle(s: Point, c1: Point, c2: Point, e: Point, t: float) -> float:
    u = 1 - t
    dx = 3 * u * u * (c1[0] - s[0]) + 6 * u * t * (c2[0] - c1[0]) + 3 * t * t * (e[0] - c2[0])
    dy = 3 * u * u * (c1[1] - s[1]) + 6 * u * t * (c2[1] - c1[1]) + 3 * t * t * (e[1] - c2[1])
    return math.atan2(dy, dx)


def elbow_points(src: Point, tgt: Point, s_side: str | None, t_side: str | None, stub: float = ELBOW_STUB) -> list[Point]:
    """A right-angled route that leaves and enters perpendicular to the box
    edges. Simpler than the editor's router: it does not avoid other shapes."""
    dx, dy = _delta(src, tgt)
    su, tu = _outward(s_side, dx, dy, True), _outward(t_side, dx, dy, False)
    a = (src[0] + su[0] * stub, src[1] + su[1] * stub)
    b = (tgt[0] + tu[0] * stub, tgt[1] + tu[1] * stub)
    if su[0] and tu[0]:  # out and in horizontally: turn halfway across
        mx = (a[0] + b[0]) / 2
        mid = [(mx, a[1]), (mx, b[1])]
    elif su[1] and tu[1]:  # out and in vertically: turn halfway down
        my = (a[1] + b[1]) / 2
        mid = [(a[0], my), (b[0], my)]
    elif su[0]:
        mid = [(b[0], a[1])]
    else:
        mid = [(a[0], b[1])]
    return _drop_collinear([src, a, *mid, b, tgt])


def _outward(side: str | None, dx: float, dy: float, leaving: bool) -> Point:
    """Unit vector out of a box through `side`. An end with no side takes
    the connector's main direction (dx, dy)."""
    if side in _SIDE_ANGLE:
        a = _SIDE_ANGLE[side]  # type: ignore[index]
        return round(math.cos(a)), round(math.sin(a))
    sign = 1.0 if leaving else -1.0
    if abs(dx) >= abs(dy):
        return (sign if dx >= 0 else -sign), 0.0
    return 0.0, (sign if dy >= 0 else -sign)


def _trim_ends(pts: list[Point], start_cap: str, end_cap: str, stroke_size: int) -> list[Point]:
    """Hide the line under arrowheads that cover it."""
    pts = list(pts)
    s_ext, s_hidden = end_stub(start_cap, stroke_size)
    t_ext, t_hidden = end_stub(end_cap, stroke_size)
    if s_hidden and len(pts) >= 2:
        pts[0] = _toward(pts[0], pts[1], s_ext)
    if t_hidden and len(pts) >= 2:
        pts[-1] = _toward(pts[-1], pts[-2], t_ext)
    return pts


def _drop_collinear(pts: list[Point]) -> list[Point]:
    """Drop repeated points, and middle points of a straight run that keeps
    its direction (a run that doubles back keeps its turning point)."""
    out: list[Point] = []
    for p in pts:
        if out and math.isclose(p[0], out[-1][0]) and math.isclose(p[1], out[-1][1]):
            continue
        if len(out) >= 2:
            (x0, y0), (x1, y1) = out[-2], out[-1]
            vertical = math.isclose(x0, x1) and math.isclose(x1, p[0]) and (y1 - y0) * (p[1] - y1) > 0
            horizontal = math.isclose(y0, y1) and math.isclose(y1, p[1]) and (x1 - x0) * (p[0] - x1) > 0
            if vertical or horizontal:
                out[-1] = p
                continue
        out.append(p)
    return out


# ------------------------------------------------------------------ vectors


def _add(a: Point, b: Point) -> Point:
    return a[0] + b[0], a[1] + b[1]


def _sub(a: Point, b: Point) -> Point:
    return a[0] - b[0], a[1] - b[1]


def _scale(a: Point, k: float) -> Point:
    return a[0] * k, a[1] * k


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _unit(a: Point) -> Point:
    n = math.hypot(*a)
    return (a[0] / n, a[1] / n) if n else (0.0, 0.0)


def _polar(angle: float | None, length: float = 1.0) -> Point:
    assert angle is not None
    return math.cos(angle) * length, math.sin(angle) * length


def _toward(a: Point, b: Point, d: float) -> Point:
    """The point `d` from a toward b (never past b)."""
    return _add(a, _scale(_unit(_sub(b, a)), min(d, _dist(a, b))))


def _delta(a: Point, b: Point) -> Point:
    return b[0] - a[0], b[1] - a[1]
