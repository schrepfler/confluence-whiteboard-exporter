from __future__ import annotations

import math

import pytest

from wb2canvas.connectors import ARROWHEADS, TENSION, End, end_stub, route, smooth_spline


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
    from wb2canvas.board import CAPS

    assert set(CAPS.values()) - {"none"} == set(ARROWHEADS)


def test_right_angled_route_keeps_right_angles_through_waypoints() -> None:
    r = route("dynamic", End((0, 0), "right"), End((300, 200), "left"), ((150, -50),))
    pts = r.points()
    assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(pts, pts[1:], strict=False))
    assert (150, -50) in pts
