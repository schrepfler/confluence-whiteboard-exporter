from __future__ import annotations

import itertools
import logging
import math
import re
from pathlib import Path

import pytest

from wb2canvas.board import from_dump
from wb2canvas.cli import _parse_shape_map
from wb2canvas.model import DumpFile
from wb2canvas.shapes import DEFAULT_SHAPE_MAP, GENERATORS, KIND_NAMES, STEREOTYPES, outline
from wb2canvas.svg import render_svg


def dump_to_svg(dump: DumpFile, **kw) -> str:
    return render_svg(from_dump(dump), **kw)


FIXTURE = Path(__file__).parent / "fixtures" / "sample_dump.json"


def _dump() -> DumpFile:
    return DumpFile.model_validate_json(FIXTURE.read_text())


def test_dump_to_svg_smoke() -> None:
    svg = dump_to_svg(_dump())
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert "wb-node" in svg
    assert "wb-edge" in svg


def test_dump_to_svg_emits_image_with_relative_path() -> None:
    svg = dump_to_svg(_dump())
    assert 'href="media/3326b2e5-07e9-49ce-9b79-36a2d5a986fe.jpeg"' in svg


def test_kind_names_cover_the_editor_enum() -> None:
    assert len(KIND_NAMES) == 89
    assert (KIND_NAMES[0], KIND_NAMES[3], KIND_NAMES[13], KIND_NAMES[88]) == (
        "sharp-rectangle", "rounded-rectangle", "database", "control-object")


def test_default_shape_map_draws_every_flowchart_kind() -> None:
    assert DEFAULT_SHAPE_MAP[0] == "rect"
    assert DEFAULT_SHAPE_MAP[3] == "rounded-rect"
    assert DEFAULT_SHAPE_MAP[13] == "cylinder"
    assert set(range(33)) <= DEFAULT_SHAPE_MAP.keys()
    assert set(DEFAULT_SHAPE_MAP.values()) <= STEREOTYPES


@pytest.mark.parametrize("name", sorted(GENERATORS.keys()))
def test_path_generators_emit_valid_d_strings(name: str) -> None:
    """Every path stereotype yields path data; all but the open comment
    brackets are closed outlines."""
    d = GENERATORS[name](0.0, 0.0, 100.0, 60.0)
    assert d.startswith("M")
    assert ("Z" in d) != name.startswith("comment-")


def test_shape_outline_unknown_kind_falls_back_to_a_sharp_rect() -> None:
    o = outline(99, 0.0, 0.0, 100.0, 60.0, DEFAULT_SHAPE_MAP)
    assert (o.name, o.is_rect, o.radius) == ("rect", True, 0.0)


def test_shape_without_a_kind_is_the_default_sharp_rectangle() -> None:
    assert outline(None, 0.0, 0.0, 100.0, 60.0, DEFAULT_SHAPE_MAP).name == "rect"


def test_shape_outline_resolves_cylinder() -> None:
    o = outline(13, 0.0, 0.0, 100.0, 60.0, DEFAULT_SHAPE_MAP)
    assert o.name == "cylinder" and o.d.startswith("M")


def test_shape_outline_respects_override_map() -> None:
    o = outline(99, 0.0, 0.0, 100.0, 60.0, {**DEFAULT_SHAPE_MAP, 99: "ellipse"})
    assert o.name == "ellipse" and o.d.startswith("M")


def test_rounded_rectangle_uses_atlassians_radius_and_sharp_has_none() -> None:
    svg = _svg_for([
        {"type": "shape", "shape": 3, "position": {"x": 0, "y": 0}, "size": {"x": 200, "y": 100}},
        {"type": "shape", "shape": 0, "position": {"x": 300, "y": 0}, "size": {"x": 200, "y": 100}},
    ])
    rounded, sharp = re.findall(r"<rect [^>]*>", svg)
    assert 'rx="20"' in rounded, "a tenth of the width"
    assert "rx=" not in sharp


def test_right_parallelogram_overhangs_its_box_like_atlassians() -> None:
    d = outline(8, 0.0, 0.0, 100.0, 50.0, DEFAULT_SHAPE_MAP).d
    assert d == "M 0 0 L 100 0 L 90 50 L -10 50 Z"


def test_parse_shape_map_accepts_kind_numbers_and_names() -> None:
    assert _parse_shape_map("database=hard-disk, 60=ellipse") == {13: "hard-disk", 60: "ellipse"}


def test_parse_shape_map_empty_returns_empty() -> None:
    assert _parse_shape_map("") == {}


@pytest.mark.parametrize("spec", ["4=ellipse,bad-entry", "databse=ellipse", "4=elipse"])
def test_parse_shape_map_rejects_bad_entries(spec: str) -> None:
    import click

    with pytest.raises(click.BadParameter):
        _parse_shape_map(spec)


def test_kinds_without_a_stereotype_are_reported(caplog) -> None:
    caplog.set_level(logging.WARNING)
    svg = _svg_for([
        {"type": "shape", "shape": 60, "position": {"x": 0, "y": 0}, "size": {"x": 50, "y": 90}},
        {"type": "shape", "shape": 60, "position": {"x": 90, "y": 0}, "size": {"x": 50, "y": 90}},
        {"type": "shape", "shape": 3, "position": {"x": 200, "y": 0}, "size": {"x": 50, "y": 90}},
    ])
    assert svg.count("<rect") == 3
    (msg,) = [r.getMessage() for r in caplog.records]
    assert "actor (60) x2" in msg and "rounded" not in msg


def test_dump_to_svg_shape_map_overrides_propagate() -> None:
    """Mapping the fixture's shape kind 3 to ellipse should produce ellipse paths."""
    svg = dump_to_svg(_dump(), shape_map={3: "ellipse"})
    # The fixture's shape uses kind 3; with override it must become a <path>, not <rect>.
    # Look for path elements with M command (path data) — at least one should exist
    # for the shape; a default-rendered fixture would have <rect> instead.
    assert "<path d=\"M " in svg


def _svg_for(elements: list[dict]) -> str:
    dump = DumpFile.model_validate(
        {"board": {"boardId": "1", "title": "t", "spaceKey": "S"}, "strategy": "clipboard",
         "elements": elements}
    )
    return dump_to_svg(dump)


def _adf(*texts: str) -> str:
    import json

    items = [{"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]}]}
             for t in texts]
    return json.dumps({"type": "doc", "content": [{"type": "bulletList", "content": items}]})


def test_free_text_element_renders_coloured_text_without_outline_or_clipping() -> None:
    svg = _svg_for([{
        "type": "text", "position": {"x": 0, "y": 0}, "size": {"x": 76, "y": 38},
        "color": {"x": 33, "y": 110, "z": 78}, "allowFlexibleWidth": True,
        "text": _adf("First list item here", "Second & third item", "Fourth bullet"),
    }])
    group = re.search(r'<g class="wb-node wb-text".*?</g>', svg).group(0)
    assert "<rect" not in group and "<path" not in group, "free text has no outline"
    assert "color:#216E4E" in group, "text takes the element colour"
    assert 'style="overflow:visible"' in group, "auto-width text is never clipped"
    assert "white-space:nowrap" in group
    for t in ("First list item here", "Second &amp; third item", "Fourth bullet"):
        assert t in group


def test_shape_text_honours_alignment() -> None:
    svg = _svg_for([{
        "type": "shape", "shape": 3, "position": {"x": 0, "y": 0}, "size": {"x": 160, "y": 160},
        "alignment": "center", "verticalAlignment": 1, "text": _adf("x"),
    }])
    assert "text-align:center" in svg
    assert 'class="node-body va-middle"' in svg


LONG = "A very long single line that certainly needs more than one hundred and sixty px"


def test_long_text_wraps_and_grows_height_inside_the_viewbox() -> None:
    svg = _svg_for([{
        "type": "shape", "shape": 3, "position": {"x": 0, "y": 0}, "size": {"x": 160, "y": 160},
        "text": _adf(LONG, LONG, LONG),
    }])
    _, vb_y, _, vb_h = map(float, re.search(r'viewBox="([^"]+)"', svg).group(1).split())
    rect_y = float(re.search(r'<rect [^>]*?\sy="(-?[0-9.]+)"', svg).group(1))
    rect_w = float(re.search(r'<rect [^>]*?\swidth="([0-9.]+)"', svg).group(1))
    rect_h = float(re.search(r'<rect [^>]*?\sheight="([0-9.]+)"', svg).group(1))
    assert rect_w == 160, "text wraps at the drawn width instead of widening the box"
    assert rect_h > 160, "the box grows taller to fit the wrapped text"
    assert vb_y + vb_h >= rect_y + rect_h, "the grown box must not be clipped by the viewBox"


def test_connector_lands_on_the_drawn_basis_box_edge() -> None:
    # Confluence draws shapes at basisPosition/basisSize, not position/size.
    svg = _svg_for([
        {"type": "shape", "shape": 3, "position": {"x": 0, "y": 50}, "size": {"x": 160, "y": 160},
         "basisPosition": {"x": 0, "y": 20}, "basisSize": {"x": 298, "y": 144}},
        {"type": "shape", "shape": 3, "position": {"x": 900, "y": 0}, "size": {"x": 160, "y": 160}},
        {"type": "connector", "sourceIndex": 0, "targetIndex": 1,
         "sourceAnchor": {"left": 1, "top": 0.5}, "targetAnchor": {"left": 0, "top": 0.5}},
    ])
    m = re.search(r'class="wb-edge"[^>]*\sd="M (-?[0-9.]+) (-?[0-9.]+) ', svg)
    # Drawn box: centred on (0, 50), 298 wide, grown from 144 to 204 high.
    assert float(m.group(1)) == pytest.approx(149), "right edge of the drawn box"
    assert float(m.group(2)) == pytest.approx(50), "vertical middle of the drawn box"


def test_static_svg_carries_no_script() -> None:
    svg = dump_to_svg(_dump())
    assert "<script" not in svg
    assert "javascript:" not in svg


def _connector(**kw) -> list[dict]:
    return [
        {"type": "shape", "shape": 0, "position": {"x": 0, "y": 0}, "size": {"x": 100, "y": 60}},
        {"type": "shape", "shape": 0, "position": {"x": 300, "y": 200}, "size": {"x": 100, "y": 60}},
        {"type": "connector", "sourceIndex": 0, "targetIndex": 1,
         "sourceAnchor": {"left": 1, "top": 0.5}, "targetAnchor": {"left": 0, "top": 0.5}, **kw},
    ]


def _edge(svg: str) -> str:
    return re.search(r'<path class="wb-edge"[^>]*>', svg).group(0)


def test_line_ends_reference_markers_defined_once_per_kind() -> None:
    svg = _svg_for(_connector(startCap=6, endCap=13))
    edge = _edge(svg)
    assert 'marker-start="url(#cap-open-diamond-1)"' in edge
    assert 'marker-end="url(#cap-crows-foot-1)"' in edge
    assert re.findall(r'<marker id="([^"]+)"', svg) == ["cap-crows-foot-1", "cap-open-diamond-1"]


def test_plain_line_ends_have_no_marker() -> None:
    svg = _svg_for(_connector(startCap=1, endCap=1))
    assert "marker-" not in _edge(svg) and "<marker" not in svg


def _segments(d: str) -> list[tuple[float, float]]:
    nums = [float(v) for v in re.findall(r"-?[0-9.]+", d)]
    return list(zip(nums[::2], nums[1::2], strict=True))


@pytest.mark.parametrize(("presentation", "routing"), [(1, "straight"), (2, "dynamic"), (3, "curved"), (None, "curved")])
def test_connector_routing_follows_its_presentation(presentation, routing) -> None:
    edge = _edge(_svg_for(_connector(presentation=presentation)))
    assert f'data-routing="{routing}"' in edge
    d = re.search(r'\sd="([^"]+)"', edge).group(1)
    assert (" C " in d) == (routing == "curved")
    if routing == "straight":
        assert len(_segments(d)) == 2
    if routing == "dynamic":
        pts = _segments(d)
        # Boxes centred on (0, 0) and (300, 200); right-middle to left-middle.
        assert pts[0] == (50, 0) and pts[-1] == (250, 200)
        assert all(a[0] == b[0] or a[1] == b[1] for a, b in itertools.pairwise(pts)), "right angles only"


def test_stroke_styles() -> None:
    def stroke(style: int) -> str:
        return re.search(r"<rect [^>]*>", _svg_for([
            {"type": "shape", "position": {"x": 0, "y": 0}, "size": {"x": 10, "y": 10}, "strokeStyle": style}
        ])).group(0)

    assert 'stroke="none"' in stroke(0)
    assert "dasharray" not in stroke(1)
    assert 'stroke-dasharray="4.5,3"' in stroke(2)
    assert 'stroke-dasharray="0,3" stroke-linecap="round"' in stroke(3)


def test_a_covering_arrowhead_reaches_the_box_edge_from_where_the_line_stops() -> None:
    svg = _svg_for(_connector(endCap=3, presentation=1))  # filled arrow, 8 long
    d = re.search(r'\sd="([^"]+)"', _edge(svg)).group(1)
    end = _segments(d)[-1]
    assert math.dist(end, (250, 200)) == pytest.approx(8, abs=0.1), "the line stops 8 short of the box edge"
    marker = re.search(r'<marker id="cap-filled-arrow-1".*?</marker>', svg).group(0)
    assert 'd="M 0 6 L 8 0 L 0 -6 Z"' in marker, "base where the line stops, tip 8 further on"
    assert 'fill="context-stroke"' in marker


def test_line_ends_scale_with_the_stroke_size() -> None:
    svg = _svg_for(_connector(endCap=2, stroke=3))
    assert 'stroke-width="6"' in _edge(svg)
    assert re.findall(r'<marker id="([^"]+)"', svg) == ["cap-arrow-3"]
    assert "M -16 11.2 L 0 0 L -16 -11.2" in svg, "chevron 10x14, scaled 1.6, tip on the end"
