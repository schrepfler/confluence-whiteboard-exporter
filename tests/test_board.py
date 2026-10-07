from __future__ import annotations

import json
import logging
import struct
from pathlib import Path

import pytest

from confluence_whiteboard_exporter.board import (
    CANVAS_METRICS,
    SVG_METRICS,
    Kind,
    Rgb,
    from_dump,
    load_board,
    node_box,
    stable_ids,
)
from confluence_whiteboard_exporter.canvas import render_canvas
from confluence_whiteboard_exporter.model import ClipboardElement, DumpFile
from confluence_whiteboard_exporter.svg import render_svg

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
    # Centred on p#; the basis box grew downward by twice the centre's shift.
    assert (s1.x, s1.y, s1.w, s1.h) == (-149, -52, 298, 204), "basis box, not the 160x160 size"
    assert s1.fill is None and s1.stroke == Rgb.parse("#1558BC") and s1.stroke_style == "dashed", "stored #0055CC, drawn in the theme"
    assert s2.fill == Rgb(10, 20, 30) and s2.shape_kind == 13
    assert [n.kind for n in board.nodes] == [Kind.SHAPE, Kind.SHAPE, Kind.IMAGE]
    assert board.node("I1").image.href == "media/abc.png"
    (edge,) = board.edges
    assert (edge.source, edge.target, edge.start_cap, edge.end_cap) == ("S1", "S2", "none", "arrow")


def test_fiber_dump_renders_in_every_format() -> None:
    board = _fiber_board()
    assert len(render_canvas(board).edges) == 1
    svg = render_svg(board)
    assert 'class="wb-edge"' in svg
    assert "<path d=\"M" in svg, "kind 13 is drawn as a cylinder"


# --------------------------------------------------------------------- layout


def test_shapes_are_centred_on_position_and_grown_from_their_basis_box() -> None:
    # Text grew this shape from 110 to 190 high, top edge fixed: its centre
    # moved 40 down. The stored 160x160 size is stale.
    board = _clip({"type": "shape", "position": {"x": -1000, "y": -760}, "size": {"x": 160, "y": 160},
                   "basisPosition": {"x": -1000, "y": -800}, "basisSize": {"x": 220, "y": 110},
                   "text": _text("A list of sources")})
    x, y, w, h = node_box(board.nodes[0], CANVAS_METRICS)
    assert (x, w) == pytest.approx((-1000 - 220 / 2, 220))
    assert (y, h) == pytest.approx((-800 - 110 / 2, 190))


@pytest.mark.parametrize("text", [None, '{"version":1,"type":"doc","content":[]}',
                                  '{"type":"doc","content":[{"type":"paragraph","content":[]}]}'])
def test_a_shape_without_text_is_drawn_at_its_basis_box(text: str | None) -> None:
    # The editor only grows a shape to fit its text; an empty shape keeps its
    # basis box whatever its (then stale) position says, here 180 right of
    # and 40 above the basis centre.
    board = _clip({"type": "shape", "shape": 3, "position": {"x": 230, "y": -180}, "text": text,
                   "basisPosition": {"x": 50, "y": -140}, "basisSize": {"x": 330, "y": 550}})
    n = board.nodes[0]
    assert (n.x, n.y, n.w, n.h) == pytest.approx((50 - 165, -140 - 275, 330, 550))


@pytest.mark.parametrize(("dx", "dy"), [(0.0, -20.0), (-15.0, 0.0)])
def test_a_shape_only_grows_downward_to_fit_its_text(dx: float, dy: float) -> None:
    # The editor's shapes have a fixed width and grow downward; a position
    # shifted up or sideways is stale, so a tall container keeps its basis box.
    board = _clip({"type": "shape", "shape": 1, "text": _text("Container"),
                   "position": {"x": 400 + dx, "y": 220 + dy},
                   "basisPosition": {"x": 400, "y": 220}, "basisSize": {"x": 320, "y": 720}})
    n = board.nodes[0]
    assert (n.x, n.y, n.w, n.h) == pytest.approx((400 - 160, 220 - 360, 320, 720))


def test_images_are_centred_on_position() -> None:
    board = _clip({"type": "image", "fileId": "f", "position": {"x": 100, "y": 50}, "size": {"x": 40, "y": 20}})
    n = board.nodes[0]
    assert (n.x, n.y, n.w, n.h) == (80, 40, 40, 20)


@pytest.mark.parametrize(("align", "left"), [("left", 0.0), ("center", -1.0), ("right", -2.0)])
def test_text_widens_away_from_its_aligned_edge(align: str, left: float) -> None:
    board = _clip({"type": "text", "position": {"x": 5, "y": 5}, "basisPosition": {"x": 5, "y": 5},
                   "basisSize": {"x": 10, "y": 10}, "alignment": align, "text": _text("Sample Text Here")})
    x, _, w, _ = node_box(board.nodes[0], SVG_METRICS)
    assert x == pytest.approx(left * (w - 10) / 2)


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
    # Left-aligned, so the basis box's left edge stays put.
    board = _clip({"type": "text", "position": {"x": -1211.8, "y": -255.3}, "size": {"x": 76, "y": 38},
                   "basisPosition": {"x": -1291.3, "y": -277.3}, "basisSize": {"x": 34, "y": 38},
                   "allowFlexibleWidth": True, "text": adf})
    x, y, w, h = node_box(board.nodes[0], SVG_METRICS)
    assert (x, y) == pytest.approx((-1291.3 - 17, -277.3 - 19))
    assert w >= 34 + 2 * 79.5 and h >= 38 + 2 * 22


@pytest.mark.parametrize("scale", [1.5, 2.0])
def test_font_scale_grows_the_text_box(scale: float) -> None:
    def box(s: float):
        b = _clip({"type": "text", "position": {"x": 0, "y": 0}, "fontScale": s, "text": _text("Sample Text Here")})
        return node_box(b.nodes[0], SVG_METRICS)

    assert box(scale)[2] > box(1.0)[2]


# ------------------------------------------------------------------- losses


def test_elements_it_cannot_draw_are_omitted_and_reported(caplog) -> None:
    caplog.set_level(logging.WARNING)
    board = _clip(
        {"type": "shape", "position": {"x": 0, "y": 0}, "size": {"x": 10, "y": 10}},
        {"type": "sticky", "position": {"x": 0, "y": 0}},
        {"type": "sticky", "position": {"x": 9, "y": 9}},
        {"type": "section", "position": {"x": 0, "y": 0}},
    )
    assert [n.kind for n in board.nodes] == [Kind.SHAPE]
    (msg,) = [r.getMessage() for r in caplog.records]
    assert "board 1" in msg and "1 section, 2 sticky" in msg


def test_a_fully_drawn_board_reports_nothing(caplog) -> None:
    caplog.set_level(logging.WARNING)
    load_board(FIXTURE)
    assert caplog.records == []


def test_connector_styles_are_named(caplog) -> None:
    board = _clip(
        {"type": "shape", "position": {"x": 0, "y": 0}, "size": {"x": 10, "y": 10}},
        {"type": "connector", "sourceIndex": 0, "targetIndex": 0, "startCap": 13, "endCap": 99,
         "presentation": 2, "strokeStyle": 3},
    )
    (edge,) = board.edges
    assert (edge.start_cap, edge.end_cap, edge.routing, edge.stroke_style) == (
        "crows-foot", "none", "dynamic", "dotted")
    assert any("line end 99" in r.getMessage() for r in caplog.records), "an unknown value is reported"


def test_fiber_dump_reads_alignment_wrapping_and_routing(caplog) -> None:
    caplog.set_level(logging.WARNING)
    fiber = {
        "board": {
            "S": {"t": "shape", "sh": 2, "a": 2, "va": 0},
            "T": {"t": "text", "a": 0, "fw": True},
            "C": {"t": "connector", "se": "S", "te": "T", "pr": 1, "sc": 5, "ec": 4},
            "N": {"t": "sticky"},
        },
        "dimensions": [{"key": "fs#T", "val": 2}],
        "zindex": ["S", "T", "C", "N"],
    }
    board = from_dump(DumpFile.model_validate({"board": META, "strategy": "fiber", "fiber_dump": fiber}))
    s, t = board.node("S"), board.node("T")
    assert (s.align, s.valign) == ("right", "top")
    assert (t.align, t.auto_width, t.font_scale) == ("center", True, 2.0)
    (edge,) = board.edges
    assert (edge.routing, edge.start_cap, edge.end_cap) == ("straight", "filled-diamond", "open-arrow")
    assert any("1 sticky" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------- waypoints


def _waypoint(connector: int, order: float, x: float, y: float) -> dict:
    return {"type": "pathWaypoint", "sourcePathIndex": connector, "order": order, "axis": 2,
            "position": {"x": x, "y": y}, "size": {"x": 1, "y": 1}}


def test_waypoints_bend_their_connector_in_order(caplog) -> None:
    caplog.set_level(logging.WARNING)
    board = _clip(
        {"type": "shape", "position": {"x": 0, "y": 0}, "size": {"x": 10, "y": 10}},
        {"type": "shape", "position": {"x": 500, "y": 0}, "size": {"x": 10, "y": 10}},
        {"type": "connector", "sourceIndex": 0, "targetIndex": 1},
        _waypoint(2, 1000278, 300, 50),
        _waypoint(2, 500439.5, 100, -50),
    )
    assert board.edges[0].waypoints == ((100, -50), (300, 50))
    assert caplog.records == [], "waypoints are drawn, not omitted"


def test_a_waypoint_of_no_connector_is_reported(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _clip({"type": "shape", "position": {"x": 0, "y": 0}, "size": {"x": 10, "y": 10}}, _waypoint(7, 1, 0, 0))
    assert "1 pathWaypoint" in caplog.text


def test_fiber_waypoints_bend_their_connector() -> None:
    fiber = {
        "board": {
            "C": {"t": "connector", "pr": 3},
            "W2": {"t": "pathWaypoint", "pi": "C", "or": 2},
            "W1": {"t": "pathWaypoint", "pi": "C", "or": 1},
        },
        "dimensions": [{"key": "bp#W1", "val": [1, 2]}, {"key": "bp#W2", "val": [3, 4]}],
        "zindex": ["C", "W2", "W1"],
    }
    board = from_dump(DumpFile.model_validate({"board": META, "strategy": "fiber", "fiber_dump": fiber}))
    assert board.edges[0].waypoints == ((1, 2), (3, 4))


def test_stored_colours_are_drawn_in_the_editors_theme() -> None:
    # Boards store the legacy palette; the editor paints its current theme's
    # token: navy text becomes near-black, the grey fill a neutral grey.
    board = _clip({"type": "shape", "position": {"x": 0, "y": 0}, "size": {"x": 10, "y": 10}, "fillEnabled": True,
                   "color": {"x": 179, "y": 185, "z": 196}, "strokeColor": {"x": 23, "y": 43, "z": 77}})
    n = board.nodes[0]
    assert (n.fill.hex, n.stroke.hex) == ("#B7B9BE", "#292A2E")


def test_a_colour_outside_the_palette_is_drawn_as_stored() -> None:
    from confluence_whiteboard_exporter.palette import drawn

    assert drawn("#123456") == "#123456"
    assert drawn("#172b4d") == "#292A2E"


def test_text_is_measured_in_the_editors_font() -> None:
    from confluence_whiteboard_exporter.board import text_width

    # Narrow letters really are narrower; widths are not a character count.
    assert text_width("iiii") < text_width("mmmm") / 2
    assert text_width("mmmm", "600") > text_width("mmmm") and text_width("mmmm", "h1") > text_width("mmmm", "600")


def test_a_font_scale_scales_text_set_at_the_editors_size() -> None:
    # The editor lays scaled text out at its own size, then scales it.
    from confluence_whiteboard_exporter.board import text_width

    assert SVG_METRICS.scaled(2).width("scaled") == pytest.approx(2 * text_width("scaled"))


@pytest.mark.parametrize(("markdown", "scale", "height"), [
    # Heights the editor's text engine gives for these blocks.
    ("# Head\nBody", 1, 60), ("Body\n# Head", 1, 66), ("# A\n## B\n### C\nBody", 1, 131),
    ("Body\n#### Head", 1, 53.5), ("###### A\n##### B", 1, 43.5), ("- a\n# Head", 1, 66),
    ("## Head\n- a", 1, 49), ("# Head\nBody", 2, 120),
])
def test_headings_are_spaced_as_in_the_editor(markdown: str, scale: float, height: float) -> None:
    from confluence_whiteboard_exporter.board import _wrapped_height

    assert _wrapped_height(markdown, 1000, SVG_METRICS.scaled(scale), pad_h=0) == pytest.approx(height)
