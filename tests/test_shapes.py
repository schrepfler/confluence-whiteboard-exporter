from __future__ import annotations

import math

import pytest

from wb2canvas.shapes import KIND_NAMES, _length, can_draw, dash_layout, drawing, is_icon, path_data, resolve, stretching_commands


def test_every_kind_is_described() -> None:
    assert len(KIND_NAMES) == 89
    assert all(can_draw(k) or is_icon(k) for k in KIND_NAMES)


@pytest.mark.parametrize(("kind", "drawn_as"), [(28, 1), (29, 4), (30, 2), (31, 6), (53, 3), (54, 2), (65, 14)])
def test_kinds_that_reuse_another_kinds_drawing(kind: int, drawn_as: int) -> None:
    assert resolve(kind) == drawn_as


def test_offsets_keep_their_size_while_fractions_stretch() -> None:
    # A rounded-rectangle corner: 35.2 in from the left whatever the width.
    for w in (100, 400):
        assert path_data([["M", [0, 0, 35.2, 0]], ["L", [1, 0, -35.2, 0]]], 0, 0, w, 50) == f"M 35.2 0 L {w - 35.2:g} 0"


def test_a_database_lid_is_a_fixed_height() -> None:
    small, tall = (drawing(13, 0, 0, 100, h) for h in (100, 400))
    assert small.text[1] == tall.text[1] == 52, "text starts below the 52-unit lid"


def test_fixed_aspect_drawings_sit_at_the_top_with_text_below() -> None:
    actor = drawing(60, 0, 0, 50, 160)
    assert actor.graphic == (0, 0, 50, 100), "the actor is twice as tall as it is wide"
    assert actor.text[1] == 100
    assert stretching_commands(60) is None, "the viewer must not stretch it"


def test_kinds_without_text_have_no_text_area() -> None:
    assert drawing(14, 0, 0, 60, 60).text is None  # summing junction


def test_unknown_kinds_are_placeholder_rectangles() -> None:
    d = drawing(99, 0, 0, 10, 10)
    assert d.placeholder and d.sections[0].d == "M 0 0 L 10 0 L 10 10 L 0 10 L 0 0 Z"


def _outline(kind: int, w: float, h: float):
    (stroke,) = [s for s in drawing(kind, 0, 0, w, h).sections if s.paint == "stroke"]
    (sub,) = stroke.subpaths
    return stroke, sub


def _dashes(layout: tuple[list[float], float], width: float = 3.0) -> list[tuple[float, float]]:
    """Visible dashes (start, end) along the path, round caps included."""
    dashes, offset = layout
    out, pos = [], -offset
    for k in range(0, len(dashes), 2):
        out.append((pos - width / 2, pos + dashes[k] + width / 2))
        pos += dashes[k] + dashes[k + 1]
    return out


def test_a_rounded_rectangle_is_dashed_evenly_from_its_top_left_corner() -> None:
    # The live board's "Dashed Box": 194.2 x 122.3, corner radius 35.2.
    stroke, sub = _outline(3, 194.2, 122.3)
    assert stroke.dash_mode == "even-left"
    assert sub.segments[0][0] == pytest.approx((0, 35.2)), "starts where the top-left corner starts"
    perimeter = 2 * (194.2 - 70.4) + 2 * (122.3 - 70.4) + 2 * math.pi * 35.2
    period = perimeter / 15  # floor(572.6 / 36) whole periods
    dashes = _dashes(dash_layout(sub, 3.0, stroke.dash_mode))
    assert len(dashes) == 15
    assert dashes[0][0] == pytest.approx(period / 24, abs=0.05)
    assert dashes[0][1] - dashes[0][0] == pytest.approx(0.6 * period, abs=0.05)
    # Measured on the live board, from the box's left edge: dashes along the
    # top edge at about 57-80, 94-117 and 131-151.
    corner = math.pi / 2 * 35.2
    top = [v - corner + 35.2 for dash in dashes[2:5] for v in dash]
    assert top == pytest.approx([58, 81, 96, 119, 134, 157], abs=1.5)


def test_other_rounded_polygons_keep_their_corners_solid() -> None:
    stroke, sub = _outline(4, 200, 120)  # diamond
    assert stroke.dash_mode == "corners"
    dashes = _dashes(dash_layout(sub, 3.0, stroke.dash_mode))
    pos, corners = 0.0, []
    for seg in sub.segments:
        length = _length(seg)
        if len(seg) == 4:
            corners.append((pos, pos + length))
        pos += length
    for a, b in corners:
        assert any(lo <= a + 1e-6 and hi >= b - 1e-6 for lo, hi in dashes), "each corner lies inside one dash"


def test_drawn_outlines_are_dashed_per_run_centred_on_dashes() -> None:
    # A 144 x 72 sharp rectangle: runs of 4 and 2 whole periods, each
    # starting and ending mid-dash, so every corner is one solid dash.
    stroke, sub = _outline(0, 144, 72)
    dashes = _dashes(dash_layout(sub, 3.0, stroke.dash_mode))
    assert len(dashes) == 4 + 2 + 4 + 2 + 1  # the last joins the first at the start corner
    assert dashes[0][0] == pytest.approx(0) and dashes[-1][1] == pytest.approx(432)
    assert dashes[1][1] - dashes[1][0] == pytest.approx(0.5 * 36 + 3), "dash core 18, plus round caps"


def test_a_run_shorter_than_a_period_is_solid() -> None:
    stroke, sub = _outline(0, 30, 20)
    assert dash_layout(sub, 3.0, stroke.dash_mode) == ([], 0.0)


@pytest.mark.parametrize(("kind", "text"), [
    # The editor's content boxes for its basic shapes, in a 200x100 box at 0,0.
    (0, (0, 0, 200, 100)),  # sharp rectangle: the whole box
    (1, (1.44, 1.44, 197.12, 97.12)),  # rectangle: less its 144 x 0.02 corner
    (3, (16.2, 16.2, 167.6, 67.6)),  # rounded rectangle: less its 144 x 0.225 corner
    (2, (100 - 100 / math.sqrt(2), 50 - 50 / math.sqrt(2), 200 / math.sqrt(2), 100 / math.sqrt(2))),  # ellipse
    (4, (50, 25, 100, 50)),  # diamond: half the box
    (5, (50, 50, 100, 50)),  # triangle: half the box, a quarter-height towards the base
    (6, (50, 0, 100, 50)),  # upside-down triangle
    (7, (50, 0, 100, 100)),  # parallelograms: half the width
])
def test_basic_shapes_put_text_in_the_editors_content_box(kind: int, text: tuple[float, ...]) -> None:
    assert drawing(kind, 0, 0, 200, 100).text == pytest.approx(text)
