from __future__ import annotations

import math

import pytest

from confluence_whiteboard_exporter.connectors import (
    ARROWHEADS,
    TENSION,
    End,
    _through_handles,
    end_stub,
    point_at,
    route,
    smooth_spline,
)


def _last(r) -> tuple[float, float]:
    return r.segments[-1][-1]


def test_curve_passes_through_every_waypoint_in_order() -> None:
    wps = ((100.0, -80.0), (220.0, 40.0))
    r = route("curved", End((0, 0), "right"), End((300, 0), "left"), wps)
    assert [seg[-1] for seg in r.segments] == [*wps, (300, 0)]


def test_curve_leaves_and_enters_perpendicular_to_the_anchored_edges() -> None:
    r = route("curved", End((0, 0), "right"), End((300, 200), "top"))
    (c1, c2, end), = r.segments
    assert c1[1] == pytest.approx(0) and c1[0] > 0, "leaves the right edge heading right"
    assert c2[0] == pytest.approx(300) and c2[1] < 200, "arrives at the top edge heading down"


def test_end_handles_are_the_editors_tension_times_the_distance() -> None:
    r = route("curved", End((0, 0), "right"), End((300, 0), "left"))
    (c1, c2, _), = r.segments
    assert c1 == pytest.approx((TENSION * 300, 0))
    assert c2 == pytest.approx((300 - TENSION * 300, 0))


def test_a_waypoints_tangent_bisects_the_directions_to_its_neighbours() -> None:
    (_, c_in, p), (c_out, _, _) = smooth_spline([(0, 0), (100, 0), (100, 100)], None, None)
    assert p == (100, 0)
    tangent = (c_out[0] - c_in[0], c_out[1] - c_in[1])
    assert math.atan2(tangent[1], tangent[0]) == pytest.approx(math.pi / 4)


def test_a_free_two_point_curve_bends_like_the_editors() -> None:
    (c1, _, _), = route("curved", End((0, 0), None), End((100, 100), None)).segments
    leave = math.atan2(c1[1], c1[0])
    assert leave == pytest.approx(math.pi / 4 + 0.68 * math.sin((math.pi / 4 - math.pi / 2) / 0.5))


@pytest.mark.parametrize(("cap", "gap"), [("none", 0), ("arrow", 0), ("slash", 0), ("filled-arrow", 8), ("open-circle", 8)])
def test_the_line_stops_where_a_covering_arrowhead_begins(cap: str, gap: float) -> None:
    r = route("straight", End((0, 0), "right"), End((100, 0), "left", cap))
    assert _last(r) == pytest.approx((100 - gap, 0))


def test_a_curve_runs_straight_under_an_arrowhead_that_needs_room() -> None:
    # "cross" keeps its line visible: a straight 12-unit stub ends the path.
    r = route("curved", End((0, 0), "right"), End((300, 0), "left", "cross"))
    assert r.segments[-1] == ((300, 0),)
    assert r.segments[-2][-1] == pytest.approx((288, 0))
    # "crows-foot" hides its stub: the path stops 12 units short.
    hidden = route("curved", End((0, 0), "right"), End((300, 0), "left", "crows-foot"))
    assert _last(hidden) == pytest.approx((288, 0))


def test_arrowheads_grow_with_the_stroke_size() -> None:
    assert end_stub("filled-arrow", 1) == (8, True)
    assert end_stub("filled-arrow", 3) == (pytest.approx(2 * 4 * 1.6), True)
    assert end_stub("arrow", 3) == (0, False)


def test_every_cap_has_an_arrowhead() -> None:
    from confluence_whiteboard_exporter.board import CAPS

    assert set(CAPS.values()) - {"none"} == set(ARROWHEADS)


def _straight_runs(r) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    runs, cur = [], r.start
    for seg in r.segments:
        if len(seg) == 1:
            runs.append((cur, seg[0]))
        cur = seg[-1]
    return runs


def test_a_waypoint_handle_pins_its_segment_as_in_the_editor() -> None:
    # An "x" handle pins a vertical segment at its x, whatever its y.
    a, b = End((0, 0), "right", box=(-100, -30, 100, 60)), End((300, 200), "left", box=(300, 170, 100, 60))
    assert _through_handles(a, b, ((150, -50),), ("x",), 1) == [(0, 0), (150, 0), (150, 200), (300, 200)]
    # A "y" handle between ends that face each other needs a stub out of each
    # box to its margin (4 x line width + 10 = 18), as the editor routes it.
    pts = _through_handles(a, b, ((150, 100),), ("y",), 1)
    assert pts == [(0, 0), (18, 0), (18, 100), (282, 100), (282, 200), (300, 200)]


def test_right_angled_bends_are_rounded() -> None:
    a, b = End((0, 0), "right", box=(-100, -30, 100, 60)), End((300, 200), "left", box=(300, 170, 100, 60))
    r = route("dynamic", a, b, ((150, -50),), axes=("x",))
    assert all(p[0] == q[0] or p[1] == q[1] for p, q in _straight_runs(r)), "straight runs are axis-aligned"
    assert [seg for seg in r.segments if len(seg) == 3], "bends are curves"
    assert _straight_runs(r)[0] == ((0, 0), (140, 0)), "the bend starts 10 before the corner"


def test_a_label_sits_at_its_proportion_of_the_path() -> None:
    r = route("straight", End((0, 0), "right"), End((200, 0), "left"))
    (x, y), angle = point_at(r, 0.25)
    assert (x, y, angle) == pytest.approx((50, 0, 0))
