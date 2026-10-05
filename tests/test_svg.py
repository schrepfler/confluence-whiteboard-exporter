from __future__ import annotations

import re
from pathlib import Path

import pytest

from wb2canvas.board import from_dump
from wb2canvas.cli import _parse_shape_map
from wb2canvas.model import DumpFile
from wb2canvas.shapes import DEFAULT_SHAPE_MAP, GENERATORS, outline
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


def test_default_shape_map_known_kinds() -> None:
    assert DEFAULT_SHAPE_MAP[3] == "rect"
    assert DEFAULT_SHAPE_MAP[13] == "cylinder"


@pytest.mark.parametrize("name", sorted(GENERATORS.keys()))
def test_path_generators_emit_valid_d_strings(name: str) -> None:
    """Every registered stereotype must produce a non-empty path-data string
    starting with M (move) and containing a Z (close) command."""
    gen = GENERATORS[name]
    d = gen(0.0, 0.0, 100.0, 60.0)
    assert d.startswith("M")
    assert "Z" in d


def test_shape_outline_unknown_kind_falls_back_to_rect() -> None:
    d, name = outline(99, 0.0, 0.0, 100.0, 60.0, DEFAULT_SHAPE_MAP)
    assert d == ""
    assert name == "rect"


def test_shape_outline_resolves_cylinder() -> None:
    d, name = outline(13, 0.0, 0.0, 100.0, 60.0, DEFAULT_SHAPE_MAP)
    assert name == "cylinder"
    assert d.startswith("M")


def test_shape_outline_respects_override_map() -> None:
    overrides = {**DEFAULT_SHAPE_MAP, 99: "ellipse"}
    d, name = outline(99, 0.0, 0.0, 100.0, 60.0, overrides)
    assert name == "ellipse"
    assert d.startswith("M")


def test_parse_shape_map_basic() -> None:
    m = _parse_shape_map("4=ellipse,5=diamond,11=note")
    assert m == {4: "ellipse", 5: "diamond", 11: "note"}


def test_parse_shape_map_empty_returns_empty() -> None:
    assert _parse_shape_map("") == {}


def test_parse_shape_map_rejects_malformed_entry() -> None:
    import click

    with pytest.raises(click.BadParameter):
        _parse_shape_map("4=ellipse,bad-entry")


def test_parse_shape_map_rejects_non_integer_key() -> None:
    import click

    with pytest.raises(click.BadParameter):
        _parse_shape_map("foo=ellipse")


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
    rect_w = float(re.search(r'<rect [^>]*?\swidth="([0-9.]+)"', svg).group(1))
    rect_h = float(re.search(r'<rect [^>]*?\sheight="([0-9.]+)"', svg).group(1))
    assert rect_w == 160, "text wraps at the drawn width instead of widening the box"
    assert rect_h > 160, "the box grows taller to fit the wrapped text"
    assert vb_y + vb_h >= rect_h, "the grown box must not be clipped by the viewBox"


def test_connector_lands_on_the_drawn_basis_box_edge() -> None:
    # Confluence draws shapes at basisPosition/basisSize, not position/size.
    svg = _svg_for([
        {"type": "shape", "shape": 3, "position": {"x": 0, "y": 50}, "size": {"x": 160, "y": 160},
         "basisPosition": {"x": 0, "y": 20}, "basisSize": {"x": 298, "y": 144}},
        {"type": "shape", "shape": 3, "position": {"x": 900, "y": 0}, "size": {"x": 160, "y": 160}},
        {"type": "connector", "sourceIndex": 0, "targetIndex": 1,
         "sourceAnchor": {"left": 1, "top": 0.5}, "targetAnchor": {"left": 0, "top": 0.5}},
    ])
    m = re.search(r'class="wb-edge"[^>]*d="M ([0-9.]+) ([0-9.]+) ', svg)
    assert float(m.group(1)) == pytest.approx(298), "right edge of the basis box"
    assert float(m.group(2)) == pytest.approx(20 + 144 / 2), "vertical middle of the basis box"
