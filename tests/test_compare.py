"""Path sampling for the comparison with the editor (wb2canvas/compare.py)."""

from __future__ import annotations

import math

import pytest

from wb2canvas.compare import _trim, editor_points, hausdorff, svg_points


def test_an_editor_right_angle_turns_the_way_its_headings_say() -> None:
    # Right 95, a quarter turn of radius 10 to heading down, down 95.
    path = {"start": [0, 0], "segments": [
        {"type": "line", "angle": 0, "length": 95},
        {"type": "arc", "radius": 10, "startAngle": 0, "endAngle": math.pi / 2, "counterClockwise": False},
        {"type": "line", "angle": math.pi / 2, "length": 95},
    ]}
    pts = editor_points(path)
    assert pts[-1] == pytest.approx((105, 105))
    assert min(math.dist(p, (95 + 10 * math.sin(math.pi / 4), 10 - 10 * math.cos(math.pi / 4))) for p in pts) < 1


def test_the_same_route_in_svg_and_editor_form_matches() -> None:
    editor = {"start": [0, 0], "segments": [{"type": "cubic-bezier", "control1": [50, 0], "control2": [50, 100],
                                             "end": [100, 100]}]}
    ours = svg_points("M 0 0 C 50 0, 50 100, 100 100")
    assert hausdorff(ours, editor_points(editor)) < 0.5


def test_trimming_drops_the_stretch_an_arrowhead_covers() -> None:
    pts = [(float(x), 0.0) for x in range(0, 101, 2)]
    assert _trim(pts, 0, 10)[-1] == (90.0, 0.0)
    assert _trim(pts, 8, 0)[0] == (8.0, 0.0)
