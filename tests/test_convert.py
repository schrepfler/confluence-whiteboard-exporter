from __future__ import annotations

from pathlib import Path

from wb2canvas.convert import dump_to_canvas
from wb2canvas.model import DumpFile


FIXTURE = Path(__file__).parent / "fixtures" / "sample_dump.json"


def _load() -> DumpFile:
    return DumpFile.model_validate_json(FIXTURE.read_text())


def test_fixture_parses() -> None:
    dump = _load()
    assert dump.dump_version == 1
    assert dump.board.boardId == "1000001"
    assert len(dump.elements) == 4
    types = sorted(e.type for e in dump.elements)
    assert types == ["connector", "image", "shape", "text"]


def test_dump_to_canvas_node_and_edge_counts() -> None:
    dump = _load()
    doc = dump_to_canvas(dump)
    assert len(doc.nodes) == 3
    assert len(doc.edges) == 1


def test_dump_to_canvas_outline_uses_stroke_color() -> None:
    """Shape with fillEnabled=false → node color comes from strokeColor."""
    dump = _load()
    doc = dump_to_canvas(dump)
    shape_node = next(n for n in doc.nodes if n.id == "n0")
    # strokeColor (151, 79, 12) is far from any Obsidian preset, so raw hex
    assert shape_node.color == "#974F0C"


def test_dump_to_canvas_filled_uses_fill_color() -> None:
    """When fillEnabled=true, color (not strokeColor) is the visible identity."""
    from wb2canvas.model import ClipboardElement, Vector2, Vector3

    elem = ClipboardElement(
        type="shape",
        position=Vector2(x=0, y=0),
        size=Vector2(x=100, y=100),
        color=Vector3(x=128, y=0, z=128),  # purple
        strokeColor=Vector3(x=10, y=10, z=10),
        fillEnabled=True,
    )
    from wb2canvas.convert import _shape_or_text_to_node
    node = _shape_or_text_to_node(elem, 0)
    assert node.color == "6"  # snapped to Obsidian's purple preset


def test_dump_to_canvas_adf_to_markdown() -> None:
    dump = _load()
    doc = dump_to_canvas(dump)
    shape_node = next(n for n in doc.nodes if n.id == "n0")
    assert shape_node.text == "**Service A**"


def test_dump_to_canvas_connector_mapping() -> None:
    dump = _load()
    doc = dump_to_canvas(dump)
    edge = doc.edges[0]
    assert edge.fromNode == "n0"
    assert edge.toNode == "n1"
    assert edge.toEnd == "arrow"
    assert edge.fromEnd == "none"


def test_dump_to_canvas_image_node_is_file_type() -> None:
    dump = _load()
    doc = dump_to_canvas(dump)
    img = next(n for n in doc.nodes if n.id == "n2")
    assert img.type == "file"
    assert img.file is not None and "3326b2e5" in img.file


def test_dump_to_canvas_z_order_matches_input() -> None:
    dump = _load()
    doc = dump_to_canvas(dump)
    assert [n.id for n in doc.nodes] == ["n0", "n1", "n2"]


def test_dump_to_canvas_anchor_to_side() -> None:
    dump = _load()
    doc = dump_to_canvas(dump)
    edge = doc.edges[0]
    assert edge.fromSide == "right"
    assert edge.toSide == "left"


def test_dump_to_canvas_image_uses_media_path() -> None:
    dump = _load()
    doc = dump_to_canvas(dump)
    img = next(n for n in doc.nodes if n.id == "n2")
    assert img.file == "media/3326b2e5-07e9-49ce-9b79-36a2d5a986fe.jpeg"


def test_dump_to_canvas_vault_prefix_applied_to_image() -> None:
    dump = _load()
    doc = dump_to_canvas(dump, vault_prefix="Whiteboards/SC")
    img = next(n for n in doc.nodes if n.id == "n2")
    assert img.file == "Whiteboards/SC/media/3326b2e5-07e9-49ce-9b79-36a2d5a986fe.jpeg"


def test_push_apart_separates_overlapping_nodes() -> None:
    from wb2canvas.convert import push_apart
    from wb2canvas.model import CanvasNode

    a = CanvasNode(id="a", type="text", x=0, y=0, width=100, height=100)
    b = CanvasNode(id="b", type="text", x=50, y=50, width=100, height=100)
    push_apart([a, b])
    # After resolution, the two rects must not overlap.
    assert not (a.x < b.x + b.width and b.x < a.x + a.width
                and a.y < b.y + b.height and b.y < a.y + a.height)


def test_push_apart_preserves_intentional_stacks() -> None:
    """Nodes at near-identical original positions are treated as authored
    stacks (e.g. the legend swatches) and left alone."""
    from wb2canvas.convert import push_apart
    from wb2canvas.model import CanvasNode

    a = CanvasNode(id="a", type="text", x=10, y=10, width=80, height=40)
    b = CanvasNode(id="b", type="text", x=12, y=12, width=80, height=40)
    push_apart([a, b])
    assert (a.x, a.y) == (10, 10)
    assert (b.x, b.y) == (12, 12)


def test_push_apart_no_op_when_no_overlap() -> None:
    from wb2canvas.convert import push_apart
    from wb2canvas.model import CanvasNode

    a = CanvasNode(id="a", type="text", x=0, y=0, width=100, height=100)
    b = CanvasNode(id="b", type="text", x=200, y=0, width=100, height=100)
    iters = push_apart([a, b])
    assert iters == 1  # one pass establishes no movement, exits
    assert (a.x, a.y, b.x, b.y) == (0, 0, 200, 0)


def test_push_apart_resolves_chain_of_three() -> None:
    """Three overlapping nodes in a row require multiple iterations."""
    from wb2canvas.convert import push_apart
    from wb2canvas.model import CanvasNode

    a = CanvasNode(id="a", type="text", x=0, y=0, width=100, height=100)
    b = CanvasNode(id="b", type="text", x=50, y=0, width=100, height=100)
    c = CanvasNode(id="c", type="text", x=100, y=0, width=100, height=100)
    push_apart([a, b, c])
    # Final state: no pair overlaps.
    nodes = [a, b, c]
    for i in range(3):
        for j in range(i + 1, 3):
            n, m = nodes[i], nodes[j]
            assert not (n.x < m.x + m.width and m.x < n.x + n.width
                        and n.y < m.y + m.height and m.y < n.y + n.height)


def test_dump_to_canvas_resolve_collisions_default_off() -> None:
    """Anti-collision is opt-in: by default the converter preserves source positions."""
    dump = _load()
    doc_default = dump_to_canvas(dump)
    doc_explicit_off = dump_to_canvas(dump, resolve_collisions=False)
    assert [(n.x, n.y) for n in doc_default.nodes] == [
        (n.x, n.y) for n in doc_explicit_off.nodes
    ]


def test_dump_to_canvas_resolve_collisions_opt_in() -> None:
    """When opted in via flag, the call path runs and returns the same node set."""
    dump = _load()
    doc_off = dump_to_canvas(dump, resolve_collisions=False)
    doc_on = dump_to_canvas(dump, resolve_collisions=True)
    # The fixture's nodes don't overlap, so on/off produce identical positions.
    assert len(doc_off.nodes) == len(doc_on.nodes) == 3


def _elem(**kw):
    from wb2canvas.model import ClipboardElement

    return ClipboardElement.model_validate(kw)


def test_rendered_bounds_uses_basis_geometry_for_shapes() -> None:
    from wb2canvas.convert import rendered_bounds

    e = _elem(type="shape", position={"x": 10, "y": 50}, size={"x": 160, "y": 160},
              basisPosition={"x": 10, "y": 11}, basisSize={"x": 298, "y": 144})
    assert rendered_bounds(e) == (10, 11, 298, 144)


def test_rendered_bounds_wraps_long_text_by_growing_height_only() -> None:
    from wb2canvas.convert import SVG_METRICS, rendered_bounds

    e = _elem(type="shape", position={"x": 0, "y": 0}, size={"x": 160, "y": 160},
              basisPosition={"x": 0, "y": 0}, basisSize={"x": 298, "y": 144})
    md = "**Service D**\n\n- " + "Breaks paragraphs where words go CompoundWordHere " * 8
    x, y, w, h = rendered_bounds(e, markdown=md, metrics=SVG_METRICS)
    assert w == 298
    assert h > 144


def test_rendered_bounds_free_text_ignores_placeholder_size() -> None:
    from wb2canvas.convert import SVG_METRICS, rendered_bounds

    e = _elem(type="text", position={"x": 5, "y": 6}, size={"x": 76, "y": 38},
              basisPosition={"x": 5, "y": 6}, basisSize={"x": 34, "y": 38}, allowFlexibleWidth=True)
    x, y, w, h = rendered_bounds(e, markdown="- First list item here\n- Second & third item\n- Fourth bullet",
                                 metrics=SVG_METRICS)
    assert (x, y) == (5, 6)
    assert w > 76 * 2, "auto-width text sizes to its longest line"
    assert h > 38 * 1.5, "three lines need more than the placeholder height"
