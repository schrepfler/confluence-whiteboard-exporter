from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from wb2canvas.board import (
    CANVAS_METRICS,
    SVG_METRICS,
    Kind,
    Rgb,
    from_dump,
    load_board,
    node_box,
    stable_ids,
)
from wb2canvas.canvas import render_canvas
from wb2canvas.model import ClipboardElement, DumpFile
from wb2canvas.svg import render_svg

FIXTURE = Path(__file__).parent / "fixtures" / "sample_dump.json"
META = {"boardId": "1", "title": "t", "spaceKey": "S"}


def _els(*raw: dict) -> list[ClipboardElement]:
    return [ClipboardElement.model_validate(r) for r in raw]


def _text(t: str) -> str:
    return json.dumps({"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]}]})


def _clip(*raw: dict):
    return from_dump(DumpFile.model_validate({"board": META, "strategy": "clipboard", "elements": list(raw)}))


# ------------------------------------------------------------------ stable ids


def test_connector_ends_use_the_real_element_ids() -> None:
    assert [n.id for n in load_board(FIXTURE).nodes][:2] == ["shape-A", "text-B"]


def test_unreferenced_element_keeps_its_id_when_another_is_inserted_before_it() -> None:
    image = {"type": "image", "fileId": "f1", "position": {"x": 0, "y": 0}, "size": {"x": 1, "y": 1}}
    label = {"type": "text", "position": {"x": 0, "y": 0}, "text": _text("Legend")}
    before = stable_ids(_els(label, image))
    after = stable_ids(_els({"type": "text", "position": {"x": 9, "y": 9}, "text": _text("New")}, label, image))
    assert after[1:] == before, "positional ids (n0, n1, ...) would all have shifted"


def test_ids_are_deterministic_across_runs() -> None:
    els = _els({"type": "text", "position": {"x": 0, "y": 0}, "text": _text("A")})
    assert stable_ids(els) == stable_ids(els)


def test_identical_elements_get_distinct_ids() -> None:
    same = {"type": "text", "position": {"x": 0, "y": 0}, "text": _text("dup")}
    ids = stable_ids(_els(same, same, same))
    assert len(set(ids)) == 3
    assert ids[1] == ids[0] + ".2"


def test_a_reference_claimed_twice_is_not_reused() -> None:
    shape = {"type": "shape", "position": {"x": 0, "y": 0}, "size": {"x": 1, "y": 1}}
    conn = {"type": "connector", "sourceIndex": 0, "sourceElement": "SAME",
            "targetIndex": 1, "targetElement": "SAME"}
    ids = stable_ids(_els(shape, shape, conn))
    assert len(set(ids)) == 3
    assert "SAME" in ids


# -------------------------------------------------------------------- adapter


def test_mojibake_from_old_dumps_is_repaired_on_load() -> None:
    # Old probes ran atob() output (one char per byte, i.e. Latin-1) straight
    # into JSON.parse, so UTF-8 "’" arrived as "â" plus two C1 controls.
    # Atlassian's JSON.stringify leaves non-ASCII unescaped.
    garbled = "Service" + "’".encode().decode("latin-1") + "s"
    adf = json.dumps({"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "text", "text": garbled}]}]}, ensure_ascii=False)
    board = _clip({"type": "text", "position": {"x": 0, "y": 0}, "text": adf})
    assert board.nodes[0].markdown == "Service’s"


def test_legitimate_accented_text_is_left_alone() -> None:
    adf = json.dumps({"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "text", "text": "château → pâté"}]}]}, ensure_ascii=False)
    board = _clip({"type": "text", "position": {"x": 0, "y": 0}, "text": adf})
    assert board.nodes[0].markdown == "château → pâté"


def test_edge_end_that_is_not_a_node_falls_back_to_its_recorded_point() -> None:
    board = _clip(
        {"type": "shape", "position": {"x": 0, "y": 0}, "size": {"x": 10, "y": 10}},
        {"type": "connector", "sourceIndex": 0, "targetIndex": 7, "end": [300, 40]},
    )
    edge = board.edges[0]
    assert edge.source == board.nodes[0].id
    assert edge.target is None and edge.end == (300.0, 40.0)


def _f32(r: float, g: float, b: float) -> list[int]:
    return list(struct.pack(">3f", r, g, b))


def _fiber_board():
    fiber = {
        "board": {
            "S1": {"t": "shape", "sh": 3, "fe": False, "c": _f32(254, 193, 118), "stc": _f32(0, 85, 204), "sts": 2},
            # Playwright may hand a Uint8Array back as an index-keyed dict.
            "S2": {"t": "shape", "sh": 13, "fe": True,
                   "c": {str(i): v for i, v in enumerate(_f32(10, 20, 30))}},
            "C1": {"t": "connector", "se": "S1", "te": "S2", "sa": {"left": 1, "top": 0.5},
                   "ta": {"left": 0, "top": 0.5}, "sc": 1, "ec": 2, "c": _f32(117, 129, 149)},
            "I1": {"t": "image", "fi": "abc"},
        },
        "dimensions": [
            {"key": "p#S1", "val": [0, 50]}, {"key": "s#S1", "val": [160, 160]},
            {"key": "bp#S1", "val": [0, 20]}, {"key": "bs#S1", "val": [298, 144]},
            {"key": "p#S2", "val": [500, 0]}, {"key": "s#S2", "val": [100, 100]},
            {"key": "p#I1", "val": [0, 400]}, {"key": "s#I1", "val": [64, 48]},
        ],
        "zindex": ["S1", "S2", "C1", "I1"],
    }
    return from_dump(DumpFile.model_validate(
        {"board": META, "strategy": "fiber", "fiber_dump": fiber, "media": {"abc": "media/abc.png"}}
    ))


def test_fiber_dump_uses_drawn_geometry_and_decodes_colours() -> None:
    board = _fiber_board()
    s1, s2 = board.node("S1"), board.node("S2")
    assert (s1.x, s1.y, s1.w, s1.h) == (0, 20, 298, 144), "basis box, not the 160x160 size"
    assert s1.fill is None and s1.stroke == Rgb(0, 85, 204) and s1.dashed
    assert s2.fill == Rgb(10, 20, 30) and s2.shape_kind == 13
    assert [n.kind for n in board.nodes] == [Kind.SHAPE, Kind.SHAPE, Kind.IMAGE]
    assert board.node("I1").image.href == "media/abc.png"
    (edge,) = board.edges
    assert (edge.source, edge.target, edge.end_arrow, edge.start_arrow) == ("S1", "S2", True, False)


def test_fiber_dump_renders_in_every_format() -> None:
    board = _fiber_board()
    assert len(render_canvas(board).edges) == 1
    svg = render_svg(board)
    assert 'class="wb-edge"' in svg
    assert "<path d=\"M" in svg, "kind 13 is drawn as a cylinder"


# --------------------------------------------------------------------- layout


def test_shapes_are_drawn_at_their_basis_box() -> None:
    board = _clip({"type": "shape", "position": {"x": 10, "y": 50}, "size": {"x": 160, "y": 160},
                   "basisPosition": {"x": 10, "y": 11}, "basisSize": {"x": 298, "y": 144}})
    assert node_box(board.nodes[0], CANVAS_METRICS) == (10, 11, 298, 144)


def test_long_shape_text_wraps_and_grows_height_only() -> None:
    long = "Breaks paragraphs where words go CompoundWordHere " * 8
    board = _clip({"type": "shape", "position": {"x": 0, "y": 0}, "size": {"x": 160, "y": 160},
                   "basisPosition": {"x": 0, "y": 0}, "basisSize": {"x": 298, "y": 144}, "text": _text(long)})
    _, _, w, h = node_box(board.nodes[0], SVG_METRICS)
    assert w == 298 and h > 144


def test_free_text_sizes_to_its_content_not_its_placeholder() -> None:
    adf = json.dumps({"type": "doc", "content": [{"type": "bulletList", "content": [
        {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]}]}
        for t in ("First list item here", "Second & third item", "Fourth bullet")]}]})
    board = _clip({"type": "text", "position": {"x": 5, "y": 6}, "size": {"x": 76, "y": 38},
                   "allowFlexibleWidth": True, "text": adf})
    x, y, w, h = node_box(board.nodes[0], SVG_METRICS)
    assert (x, y) == (5, 6)
    assert w > 76 * 2 and h > 38 * 1.5


@pytest.mark.parametrize("scale", [1.5, 2.0])
def test_font_scale_grows_the_text_box(scale: float) -> None:
    def box(s: float):
        b = _clip({"type": "text", "position": {"x": 0, "y": 0}, "fontScale": s, "text": _text("Sample Text Here")})
        return node_box(b.nodes[0], SVG_METRICS)

    assert box(scale)[2] > box(1.0)[2]
