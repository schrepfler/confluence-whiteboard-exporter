from __future__ import annotations

from pathlib import Path

import pytest

from wb2canvas.cli import _parse_shape_map
from wb2canvas.model import DumpFile
from wb2canvas.svg import (
    DEFAULT_SHAPE_MAP,
    _PATH_GENERATORS,
    _shape_outline,
    dump_to_svg,
)


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


def test_dump_to_svg_vault_prefix_prepended_to_image() -> None:
    svg = dump_to_svg(_dump(), vault_prefix="Whiteboards/SC")
    assert 'href="Whiteboards/SC/media/3326b2e5-07e9-49ce-9b79-36a2d5a986fe.jpeg"' in svg


def test_default_shape_map_known_kinds() -> None:
    assert DEFAULT_SHAPE_MAP[3] == "rect"
    assert DEFAULT_SHAPE_MAP[13] == "cylinder"


@pytest.mark.parametrize("name", sorted(_PATH_GENERATORS.keys()))
def test_path_generators_emit_valid_d_strings(name: str) -> None:
    """Every registered stereotype must produce a non-empty path-data string
    starting with M (move) and containing a Z (close) command."""
    gen = _PATH_GENERATORS[name]
    d = gen(0.0, 0.0, 100.0, 60.0)
    assert d.startswith("M")
    assert "Z" in d


def test_shape_outline_unknown_kind_falls_back_to_rect() -> None:
    d, name = _shape_outline(99, 0.0, 0.0, 100.0, 60.0, DEFAULT_SHAPE_MAP)
    assert d == ""
    assert name == "rect"


def test_shape_outline_resolves_cylinder() -> None:
    d, name = _shape_outline(13, 0.0, 0.0, 100.0, 60.0, DEFAULT_SHAPE_MAP)
    assert name == "cylinder"
    assert d.startswith("M")


def test_shape_outline_respects_override_map() -> None:
    overrides = {**DEFAULT_SHAPE_MAP, 99: "ellipse"}
    d, name = _shape_outline(99, 0.0, 0.0, 100.0, 60.0, overrides)
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
