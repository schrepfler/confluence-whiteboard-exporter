from __future__ import annotations

import logging
from pathlib import Path

import pytest

from wb2canvas.board import from_dump, load_board
from wb2canvas.canvas import CanvasDoc, CanvasNode, push_apart, render_canvas
from wb2canvas.model import DumpFile

FIXTURE = Path(__file__).parent / "fixtures" / "sample_dump.json"
IMAGE_HREF = "media/3326b2e5-07e9-49ce-9b79-36a2d5a986fe.jpeg"


def _doc(**kw) -> CanvasDoc:
    return render_canvas(load_board(FIXTURE), **kw)


def _node(doc: CanvasDoc, node_id: str) -> CanvasNode:
    return next(n for n in doc.nodes if n.id == node_id)


def test_fixture_parses() -> None:
    dump = DumpFile.model_validate_json(FIXTURE.read_text())
    assert dump.dump_version == 1
    assert dump.board.boardId == "1000001"
    assert sorted(e.type for e in dump.elements) == ["connector", "image", "shape", "text"]


def test_node_and_edge_counts() -> None:
    doc = _doc()
    assert len(doc.nodes) == 3
    assert len(doc.edges) == 1


def test_unfilled_shape_is_coloured_by_its_outline() -> None:
    # strokeColor (151, 79, 12) is far from every Obsidian preset: raw hex.
    assert _node(_doc(), "shape-A").color == "#974F0C"


def test_filled_shape_is_coloured_by_its_fill_snapped_to_preset() -> None:
    dump = DumpFile.model_validate({
        "board": {"boardId": "1", "title": "t", "spaceKey": "S"}, "strategy": "clipboard",
        "elements": [{"type": "shape", "position": {"x": 0, "y": 0}, "size": {"x": 100, "y": 100},
                      "color": {"x": 128, "y": 0, "z": 128}, "strokeColor": {"x": 10, "y": 10, "z": 10},
                      "fillEnabled": True}],
    })
    assert render_canvas(from_dump(dump)).nodes[0].color == "6"  # Obsidian purple


def test_shape_text_is_markdown() -> None:
    assert _node(_doc(), "shape-A").text == "**Service A**"


def test_connector_joins_the_elements_it_names() -> None:
    edge = _doc().edges[0]
    assert (edge.fromNode, edge.toNode) == ("shape-A", "text-B")
    assert (edge.fromEnd, edge.toEnd) == ("none", "arrow")
    assert (edge.fromSide, edge.toSide) == ("right", "left")


def test_image_becomes_a_file_node() -> None:
    img = next(n for n in _doc().nodes if n.type == "file")
    assert img.file == IMAGE_HREF


def test_vault_prefix_is_prepended_to_image_paths() -> None:
    img = next(n for n in _doc(vault_prefix="Whiteboards/SC/").nodes if n.type == "file")
    assert img.file == f"Whiteboards/SC/{IMAGE_HREF}"


def test_z_order_follows_the_source() -> None:
    ids = [n.id for n in _doc().nodes]
    assert ids[:2] == ["shape-A", "text-B"]
    assert ids[2].startswith("h:")  # the image is not a connector end: content id


def test_edge_with_an_end_off_the_board_is_dropped_and_reported(caplog) -> None:
    caplog.set_level(logging.WARNING)
    dump = DumpFile.model_validate_json(FIXTURE.read_text())
    dump.elements[3].targetIndex = 99
    assert render_canvas(from_dump(dump)).edges == []
    assert "omitted 1 connector" in caplog.text


@pytest.mark.parametrize(("cap", "end"), [(2, "arrow"), (3, "arrow"), (4, "arrow"), (1, "none"), (13, "none")])
def test_arrowheads_become_arrows_and_other_line_ends_are_dropped(caplog, cap: int, end: str) -> None:
    caplog.set_level(logging.WARNING)
    dump = DumpFile.model_validate_json(FIXTURE.read_text())
    dump.elements[3].endCap = cap
    assert render_canvas(from_dump(dump)).edges[0].toEnd == end
    assert ("crows-foot" in caplog.text) == (cap == 13)


def test_collision_pass_is_off_by_default() -> None:
    base = [(n.x, n.y) for n in _doc().nodes]
    assert base == [(n.x, n.y) for n in _doc(resolve_collisions=False).nodes]


def test_collision_pass_opt_in_runs() -> None:
    assert len(_doc(resolve_collisions=True).nodes) == 3


def _overlap(n: CanvasNode, m: CanvasNode) -> bool:
    return n.x < m.x + m.width and m.x < n.x + n.width and n.y < m.y + m.height and m.y < n.y + n.height


def test_push_apart_separates_overlapping_nodes() -> None:
    a = CanvasNode(id="a", type="text", x=0, y=0, width=100, height=100)
    b = CanvasNode(id="b", type="text", x=50, y=50, width=100, height=100)
    push_apart([a, b])
    assert not _overlap(a, b)


def test_push_apart_leaves_deliberate_overlays_alone() -> None:
    a = CanvasNode(id="a", type="text", x=10, y=10, width=80, height=40)
    b = CanvasNode(id="b", type="text", x=12, y=12, width=80, height=40)
    push_apart([a, b])
    assert (a.x, a.y, b.x, b.y) == (10, 10, 12, 12)


def test_push_apart_no_op_when_no_overlap() -> None:
    a = CanvasNode(id="a", type="text", x=0, y=0, width=100, height=100)
    b = CanvasNode(id="b", type="text", x=200, y=0, width=100, height=100)
    assert push_apart([a, b]) == 1
    assert (a.x, a.y, b.x, b.y) == (0, 0, 200, 0)


def test_push_apart_resolves_chain_of_three() -> None:
    nodes = [CanvasNode(id=k, type="text", x=x, y=0, width=100, height=100)
             for k, x in (("a", 0), ("b", 50), ("c", 100))]
    push_apart(nodes)
    assert not any(_overlap(nodes[i], nodes[j]) for i in range(3) for j in range(i + 1, 3))
