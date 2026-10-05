from __future__ import annotations

import pytest

from wb2canvas.shapes import KIND_NAMES, can_draw, drawing, is_icon, path_data, resolve, stretching_commands


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
    assert d.placeholder and d.is_rect
