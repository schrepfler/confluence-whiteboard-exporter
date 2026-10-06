"""The reference board's spec and payload (see docs/plan.md)."""

from __future__ import annotations

import base64
import json
import re
from html import escape

from wb2canvas.board import CAPS, from_dump
from wb2canvas.model import ClipboardElement, DumpFile
from wb2canvas.reference import CELL_H, CELL_W, COLUMNS, clipboard_html, payload, spec, spec_hash
from wb2canvas.shapes import KIND_NAMES
from wb2canvas.svg import render_svg

CELLS = spec()
PAYLOAD = payload(CELLS)


def _caption(e: dict) -> str | None:
    if e["type"] != "text":
        return None
    first = json.loads(e["text"])["content"][0]
    return first["content"][0].get("text") if first["type"] == "paragraph" else None


def test_every_cell_has_one_unique_caption() -> None:
    captions = [c for e in PAYLOAD if (c := _caption(e)) in {cell.name for cell in CELLS}]
    assert sorted(captions) == sorted(cell.name for cell in CELLS)
    assert len(set(captions)) == len(captions)


def test_indices_point_at_the_right_elements() -> None:
    for e in PAYLOAD:
        for key in ("sourceIndex", "targetIndex"):
            if e.get(key, -1) >= 0:
                assert PAYLOAD[e[key]]["type"] in ("shape", "text"), e
        if "sourcePathIndex" in e:
            assert PAYLOAD[e["sourcePathIndex"]]["type"] == "connector", e


def test_each_cell_stays_inside_its_place_in_the_grid() -> None:
    n = 0
    for i, cell in enumerate(CELLS):
        cx, cy = (i % COLUMNS) * CELL_W, (i // COLUMNS) * CELL_H
        for e in PAYLOAD[n:n + len(cell.elements) + 1]:
            if e["type"] in ("shape", "text", "advanced-icon"):
                assert abs(e["position"]["x"] - cx) < CELL_W / 2 and abs(e["position"]["y"] - cy) < CELL_H / 2, cell.name
        n += len(cell.elements) + 1


def test_the_spec_covers_every_kind_end_routing_and_label_side() -> None:
    kinds = {e["shape"] for e in PAYLOAD if e["type"] == "shape"}
    assert set(KIND_NAMES) <= kinds
    connectors = [e for e in PAYLOAD if e["type"] == "connector"]
    assert {e["endCap"] for e in connectors} == set(CAPS)
    assert {e["presentation"] for e in connectors} == {1, 2, 3}
    assert {e["pathOffsetPosition"] for e in PAYLOAD if e["type"] == "pathLabel"} == {0, 1, 2}
    assert {e["axis"] for e in PAYLOAD if e["type"] == "pathWaypoint"} == {0, 1, 2}


def test_the_clipboard_html_is_what_the_editor_reads() -> None:
    html = clipboard_html(PAYLOAD)
    assert re.fullmatch(r'<div id="canvas-clipboard" data-canvas-clipboard="[A-Za-z0-9+/=]+"></div>', html)
    data = re.search(r'data-canvas-clipboard="([^"]+)"', html).group(1)
    assert json.loads(base64.b64decode(data).decode("utf-8")) == PAYLOAD


def test_the_payload_renders_with_only_the_expected_warnings(caplog) -> None:
    dump = DumpFile.model_validate({"board": {"boardId": "1", "title": "reference", "spaceKey": "S"},
                                    "strategy": "clipboard",
                                    "elements": [ClipboardElement.model_validate(e) for e in PAYLOAD]})
    board = from_dump(dump)
    svg = render_svg(board)
    assert all(escape(cell.name, quote=False) in svg for cell in CELLS)
    assert "omitted" not in caplog.text and "unknown" not in caplog.text, caplog.text


def test_the_hash_changes_with_the_spec() -> None:
    assert spec_hash(PAYLOAD) == spec_hash(payload(spec()))
    assert spec_hash(PAYLOAD) != spec_hash(PAYLOAD[1:])
