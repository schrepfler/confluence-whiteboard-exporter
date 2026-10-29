"""The viewer's two homes: a page holding the SVG, and the SVG itself.

The viewer (viewer.js) is one script that attaches to an exported SVG,
reading what it needs from the SVG's data attributes, so any host can
attach it: this page, the SVG opened on its own, or another application.
The SVG drawing (svg.py) stays script-free; with --static-svg it is
written as is, for places that strip or block SVG carrying script
(Obsidian embeds, many mail filters).
"""

from __future__ import annotations

from pathlib import Path

from .adf import _xml_escape
from .board import Board
from .svg import svg_document, warn_placeholders

VIEWER_JS = (Path(__file__).parent / "viewer.js").read_text()

_PAGE_CSS = (
    "html,body{margin:0;height:100%;overflow:hidden;background:#fff;}"
    "svg{display:block;width:100%;height:100%;}"
)
# Attaches the viewer once the document is parsed, the board filling the window.
_ATTACH = (
    "(function () {{ function go() {{ WhiteboardViewer.attach({target}, {{ mode: 'window' }}); }}"
    " if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go(); }})();"
)


def render_html(board: Board, shape_map: dict[int, int] | None = None) -> str:
    title = _xml_escape(board.meta.title or board.meta.boardId)
    svg, placeholders = svg_document(board, shape_map)
    warn_placeholders("html", board, placeholders)
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{title}</title>",
            f"<style>{_PAGE_CSS}</style>",
            "</head>",
            "<body>",
            svg,
            f"<script>{VIEWER_JS}</script>",
            f"<script>{_ATTACH.format(target='document.body')}</script>",
            "</body>",
            "</html>",
        ]
    )


def render_interactive_svg(board: Board, shape_map: dict[int, int] | None = None) -> str:
    """The SVG with the viewer inside it, attached to itself: drag, pan and
    zoom when the file is opened on its own in a browser. Where scripts are
    stripped (an Obsidian embed, an <img>) it is the static drawing."""
    svg, placeholders = svg_document(board, shape_map)
    warn_placeholders("svg", board, placeholders)
    if not svg.endswith("</svg>"):
        return svg
    script = (VIEWER_JS + _ATTACH.format(target="document.documentElement")).replace("]]>", "]]]]><![CDATA[>")
    return svg.removesuffix("</svg>") + f"<script><![CDATA[{script}]]></script>\n</svg>"
