from __future__ import annotations

import logging
import math
import re
from pathlib import Path

import pytest

from wb2canvas.board import from_dump
from wb2canvas.cli import _parse_shape_map
from wb2canvas.model import DumpFile
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


def test_parse_shape_map_accepts_kind_numbers_and_names() -> None:
    assert _parse_shape_map("server=database, 60=ellipse") == {34: 13, 60: 2}


def test_parse_shape_map_empty_returns_empty() -> None:
    assert _parse_shape_map("") == {}


@pytest.mark.parametrize("spec", ["4=ellipse,bad-entry", "databse=ellipse", "4=elipse", "4=server", "4=99"])
def test_parse_shape_map_rejects_bad_entries(spec: str) -> None:
    import click

    with pytest.raises(click.BadParameter):
        _parse_shape_map(spec)


def _shape_group(svg: str) -> str:
    return re.search(r'<g class="wb-node" .*?</g>', svg).group(0)


def test_shapes_are_drawn_from_the_editors_definitions() -> None:
    svg = _svg_for([{"type": "shape", "shape": 3, "position": {"x": 100, "y": 50}, "size": {"x": 200, "y": 100}}])
    group = _shape_group(svg)
    assert 'data-kind="3"' in group and "<rect" not in group
    # The rounded corner keeps the editor's fixed 35.2 radius, whatever the
    # box: the top-left corner of the 200x100 box runs from (0, 35.2) to (35.2, 0).
    assert 'd="M 0 35.2 C 0 15.8 15.8 0 35.2 0 L 164.8 0 C' in group


def test_a_sharp_rectangle_has_square_corners() -> None:
    group = _shape_group(_svg_for([{"type": "shape", "shape": 0, "position": {"x": 0, "y": 0}, "size": {"x": 10, "y": 10}}]))
    assert 'd="M -5 -5 L 5 -5 L 5 5 L -5 5 L -5 -5 Z"' in group


def test_a_kind_that_reuses_a_drawing_is_drawn_with_it() -> None:
    def outline(kind: int) -> str:
        group = _shape_group(_svg_for([{"type": "shape", "shape": kind, "position": {"x": 0, "y": 0},
                                        "size": {"x": 100, "y": 60}}]))
        return re.sub(r'data-(kind|id)="[^"]+"', "", group)

    assert outline(29) == outline(4), "decision reuses the diamond"


def test_a_section_painted_with_the_stroke_colour_ignores_the_fill() -> None:
    # The UML start node is a solid dot in the line colour, filled or not.
    group = _shape_group(_svg_for([{"type": "shape", "shape": 80, "position": {"x": 0, "y": 0},
                                    "size": {"x": 60, "y": 60}, "strokeColor": {"x": 0, "y": 85, "z": 204}}]))
    assert 'fill="#1558BC"' in group, "stored #0055CC, drawn in the theme"


def test_text_goes_in_the_drawings_text_area() -> None:
    # A database's text sits below its lid, 52 units from the top.
    group = _shape_group(_svg_for([{"type": "shape", "shape": 13, "position": {"x": 50, "y": 50},
                                    "size": {"x": 100, "y": 100}, "text": _adf("x")}]))
    fo = re.search(r'<foreignObject x="([^"]+)" y="([^"]+)"', group)
    assert float(fo.group(2)) == pytest.approx(52)


def test_icons_are_placeholders_with_their_label_below_and_are_reported(caplog) -> None:
    caplog.set_level(logging.WARNING)
    svg = _svg_for([
        {"type": "shape", "shape": 34, "position": {"x": 50, "y": 75}, "size": {"x": 100, "y": 150}, "text": _adf("api")},
        {"type": "shape", "shape": 34, "position": {"x": 250, "y": 75}, "size": {"x": 100, "y": 150}},
    ])
    group = _shape_group(svg)
    fo_y = float(re.search(r'<foreignObject x="[^"]+" y="([^"]+)"', group).group(1))
    assert fo_y == pytest.approx(100), "label below the square icon area"
    (msg,) = [r.getMessage() for r in caplog.records]
    assert "server (34) x2" in msg and "placeholders" in msg


def test_shape_map_draws_a_kind_as_another() -> None:
    svg = dump_to_svg(_dump(), shape_map={3: 2})
    assert 'data-kind="2"' in _shape_group(svg)


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
    y, w, h = (float(re.search(rf'data-{a}="(-?[0-9.]+)"', svg).group(1)) for a in "ywh")
    assert w == 160, "text wraps at the drawn width instead of widening the box"
    assert h > 160, "the box grows taller to fit the wrapped text"
    assert vb_y + vb_h >= y + h, "the grown box must not be clipped by the viewBox"


def test_connector_lands_on_the_drawn_basis_box_edge() -> None:
    # Confluence draws shapes at basisPosition/basisSize, not position/size.
    svg = _svg_for([
        {"type": "shape", "shape": 3, "position": {"x": 0, "y": 50}, "size": {"x": 160, "y": 160},
         "basisPosition": {"x": 0, "y": 20}, "basisSize": {"x": 298, "y": 144}, "text": _adf("Service E")},
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
    assert (" C " in d) == (routing != "straight"), "curves, or right angles with rounded bends"
    if routing == "straight":
        assert len(_segments(d)) == 2
    if routing == "dynamic":
        pts = _segments(d)
        # Boxes centred on (0, 0) and (300, 200); right-middle to left-middle,
        # with rounded bends.
        assert pts[0] == (50, 0) and pts[-1] == (250, 200)
        assert " C " in d


def test_shape_outlines_are_three_units_wide_in_every_style() -> None:
    def outline(style: int) -> str | None:
        group = _shape_group(_svg_for([
            {"type": "shape", "position": {"x": 0, "y": 0}, "size": {"x": 144, "y": 72}, "strokeStyle": style}
        ]))
        m = re.search(r'<path data-part="1" data-sub="0"[^>]*>', group)
        return m and m.group(0)

    assert outline(0) is None, "no outline"
    assert 'stroke-width="3"' in outline(1) and "dasharray" not in outline(1)
    dashed = outline(2)
    assert 'stroke-width="3"' in dashed and 'stroke-linecap="round"' in dashed
    # A sharp rectangle's edges start and end mid-dash: core 18, gap 18.
    assert re.search(r'stroke-dasharray="7.5,18,18,18,18,', dashed)


@pytest.mark.parametrize(("style", "pattern"), [(2, 'stroke-dasharray="12.4,11.6"'), (3, 'stroke-dasharray="0,4.8"')])
def test_connector_patterns_follow_the_editor(style: int, pattern: str) -> None:
    edge = _edge(_svg_for(_connector(strokeStyle=style, presentation=1)))
    assert 'stroke-width="2"' in edge and pattern in edge and 'stroke-linecap="round"' in edge


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


def _labelled(**label) -> list[dict]:
    return [*_connector(presentation=1), {"type": "pathLabel", "sourcePathIndex": 2, "proportion": 0.5,
                                         "pathOffsetPosition": 0, "text": _adf("Calls"),
                                         "color": {"x": 23, "y": 43, "z": 77}, **label}]


def test_a_connector_label_sits_on_the_line_above_every_connector() -> None:
    svg = _svg_for(_labelled())
    group = re.search(r'<g class="wb-label".*?</g>', svg).group(0)
    assert "Calls" in group and "color:#292A2E" in group
    # Halfway along the straight line from (50, 0) to (250, 200).
    assert 'data-x="150" data-y="100"' in group
    assert svg.index('class="wb-label"') > svg.rindex('class="wb-edge"'), "drawn after the connectors"


def test_a_label_of_no_connector_is_reported(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _svg_for(_labelled(sourcePathIndex=9))
    assert "1 pathLabel" in caplog.text


def test_a_library_icon_is_a_named_placeholder(caplog) -> None:
    caplog.set_level(logging.WARNING)
    svg = _svg_for([{"type": "advanced-icon", "position": {"x": 0, "y": 0}, "size": {"x": 160, "y": 160},
                     "basisPosition": {"x": 0, "y": 0}, "basisSize": {"x": 100, "y": 100},
                     "iconId": "Amazon-Simple-Storage-Service", "collection": "aws"}])
    assert "Amazon Simple Storage Service" in svg
    assert re.search(r'<rect x="-50" y="-50" width="100" height="100"', svg), "drawn at its basis box, not the stale size"
    assert "1 library icon(s) drawn as placeholders" in caplog.text


def test_text_uses_the_editors_type_and_spacing() -> None:
    # The editor's whiteboard paragraphs: 11.6/.75 px on a 22 px line, in
    # Atlassian Sans; an h1 is 27 px on a 32 px line.
    svg = dump_to_svg(_dump())
    assert 'font-size="15.5"' in svg and '"Atlassian Sans", ui-sans-serif' in svg
    assert f"line-height:{22 / (11.6 / 0.75):.4f}" in svg
    assert f".node-body h1{{font-size:{27 / (11.6 / 0.75):.4f}em;line-height:{32 / 27:.4f}" in svg
