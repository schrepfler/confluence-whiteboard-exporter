"""The reference report's pixel score and statuses (wb2canvas/report.py)."""

from __future__ import annotations

import pytest

pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from wb2canvas.reference import Cell, icon, shape  # noqa: E402
from wb2canvas.report import pixel_difference, placeholder  # noqa: E402


def _box(x: int, y: int) -> Image.Image:
    im = Image.new("RGB", (120, 80), "white")
    ImageDraw.Draw(im).rectangle((x, y, x + 60, y + 40), outline=(41, 42, 46), width=3)
    return im


def test_the_same_drawing_scores_nothing() -> None:
    score, overlay = pixel_difference(_box(20, 20), _box(20, 20))
    assert score == 0 and overlay.size == (120, 80)


def test_a_pixel_of_placement_does_not_count_but_a_misplaced_mark_does() -> None:
    assert pixel_difference(_box(20, 20), _box(21, 20))[0] == 0
    assert pixel_difference(_box(20, 20), _box(26, 20))[0] > 0.3


def test_the_canvas_dot_grid_is_background() -> None:
    dotted = _box(20, 20)
    for x in range(0, 120, 8):
        for y in range(0, 80, 8):
            dotted.putpixel((x, y), (235, 236, 238))
    assert pixel_difference(dotted, _box(20, 20))[0] == 0


def test_cells_drawn_as_placeholders_on_purpose_are_known() -> None:
    assert placeholder(Cell("icon shape", [shape(33, 0, 0, 100, 100, "key")])) is not None
    assert placeholder(Cell("library icon", [icon(0, 0, "Lambda", "compute")])) is not None
    assert placeholder(Cell("rectangle", [shape(1, 0, 0, 200, 120, "rectangle")])) is None
