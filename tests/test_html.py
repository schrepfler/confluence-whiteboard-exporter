from __future__ import annotations

from pathlib import Path

from confluence_whiteboard_exporter.board import load_board
from confluence_whiteboard_exporter.html import render_html
from confluence_whiteboard_exporter.svg import render_svg

FIXTURE = Path(__file__).parent / "fixtures" / "sample_dump.json"


def test_viewer_wraps_the_static_svg_with_its_script() -> None:
    board = load_board(FIXTURE)
    page = render_html(board)
    assert page.startswith("<!doctype html>")
    assert render_svg(board) in page, "the viewer shows exactly the static replica"
    assert page.count("<script>") == 1
    assert "data-id" in page and "getAttribute('data-id')" in page


def test_viewer_title_is_escaped() -> None:
    board = load_board(FIXTURE)
    board.meta.title = '</title><script>alert(1)</script>'
    page = render_html(board)
    assert "<title>&lt;/title&gt;&lt;script&gt;alert(1)&lt;/script&gt;</title>" in page
