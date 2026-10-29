from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from confluence_whiteboard_exporter.board import load_board
from confluence_whiteboard_exporter.deploy import render
from confluence_whiteboard_exporter.html import VIEWER_JS, render_html, render_interactive_svg
from confluence_whiteboard_exporter.svg import render_svg

FIXTURE = Path(__file__).parent / "fixtures" / "sample_dump.json"


def test_viewer_wraps_the_static_svg_with_its_script() -> None:
    board = load_board(FIXTURE)
    page = render_html(board)
    assert page.startswith("<!doctype html>")
    assert render_svg(board) in page, "the viewer shows exactly the static replica"
    assert page.count("<script>") == 2, "the viewer, then attaching it to the page"
    assert VIEWER_JS in page and "WhiteboardViewer.attach(document.body, { mode: 'window' })" in page


def test_viewer_title_is_escaped() -> None:
    board = load_board(FIXTURE)
    board.meta.title = '</title><script>alert(1)</script>'
    page = render_html(board)
    assert "<title>&lt;/title&gt;&lt;script&gt;alert(1)&lt;/script&gt;</title>" in page


def test_the_svg_carries_the_viewer_script_unless_asked_not_to() -> None:
    board = load_board(FIXTURE)
    svg, _ = render(board, "svg")
    assert svg == render_interactive_svg(board)
    static = render_svg(board)
    assert svg.startswith(static.removesuffix("</svg>")), "the same drawing, with the script after it"
    assert svg.count("<script><![CDATA[") == 1 and "getAttribute('data-id')" in svg
    root = ET.fromstring(svg)  # still well-formed XML, as a standalone .svg must be
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert render(board, "svg", static_svg=True)[0] == static


def test_every_svg_carries_what_the_viewer_needs() -> None:
    board = load_board(FIXTURE)
    for svg in (render_svg(board), render_interactive_svg(board)):
        root = ET.fromstring(svg)
        config = json.loads(root.get("data-wb-config"))
        assert root.get("data-wb-board") == board.meta.boardId
        assert config["version"] == 1 and {"tension", "elbowStub", "dash", "shapes"} <= set(config)
    assert "<script" not in render_svg(board), "the static drawing still has no script"
