"""SVG renderer: a static visual replica of the whiteboard.

Unlike JSON Canvas, SVG can show what Confluence draws: dashed outlines,
shape stereotypes, coloured free text, curved connectors and dividers.
The output carries no script, so it is safe to embed or share; the
interactive viewer in html.py wraps it. Image hrefs are relative to the
SVG file, so it must sit next to `media/`.
"""

from __future__ import annotations

import logging
import math
from collections import Counter

from .adf import _xml_escape
from .board import SVG_METRICS, Board, Box, Edge, Kind, Node, Point, Rgb, anchor_point, anchor_side, layout
from .connectors import ARROWHEAD_SCALE, ARROWHEADS, End, Route, end_stub, route, thickness
from .shapes import Section, drawing, fmt, kind_label, resolve

log = logging.getLogger(__name__)

DEFAULT_STROKE = "#172B4D"
EDGE_DEFAULT_STROKE = "#758195"
MARGIN = 60
BASE_FONT_PX = 13.0


class _Bounds:
    """Extent of everything actually drawn, so the viewBox fits the drawing."""

    def __init__(self) -> None:
        self.x0 = self.y0 = math.inf
        self.x1 = self.y1 = -math.inf

    def point(self, x: float, y: float) -> None:
        self.x0, self.y0 = min(self.x0, x), min(self.y0, y)
        self.x1, self.y1 = max(self.x1, x), max(self.y1, y)

    def box(self, x: float, y: float, w: float, h: float) -> None:
        self.point(x, y)
        self.point(x + w, y + h)

    @property
    def empty(self) -> bool:
        return self.x0 == math.inf


def render_svg(board: Board, shape_map: dict[int, int] | None = None) -> str:
    svg, placeholders = svg_document(board, shape_map)
    warn_placeholders("svg", board, placeholders)
    return svg


def warn_placeholders(fmt_name: str, board: Board, placeholders: Counter[int]) -> None:
    if placeholders:
        counts = sorted(placeholders.items())
        kinds = ", ".join(kind_label(k) + (f" x{n}" if n > 1 else "") for k, n in counts)
        log.warning("board %s %s: no drawing for shape kinds %s; drawn as placeholders",
                    board.meta.boardId, fmt_name, kinds)


def svg_document(board: Board, shape_map: dict[int, int] | None = None) -> tuple[str, Counter[int]]:
    """The SVG markup, and how many shapes of each kind had no drawing."""
    smap = shape_map or {}
    boxes = layout(board, SVG_METRICS)
    bounds = _Bounds()
    placeholders: Counter[int] = Counter(
        d.kind for n in board.nodes if n.kind is Kind.SHAPE and (d := drawing(n.shape_kind, 0, 0, 1, 1, smap)).placeholder
    )

    # Nodes in source z-order; connectors last so arrowheads stay on top.
    node_parts: list[str] = []
    for node in board.nodes:
        box = boxes[node.id]
        if node.kind is Kind.LINE:
            for p in node.points:
                bounds.point(*p)
        else:
            bounds.box(*box)
        if markup := _render_node(node, box, smap):
            node_parts.append(markup)
    edge_parts = [m for e in board.edges if (m := _render_edge(e, boxes, bounds))]
    caps = {(c, e.stroke_size) for e in board.edges for c in (e.start_cap, e.end_cap) if c in ARROWHEADS}

    if bounds.empty:
        empty = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100"></svg>'
        return empty, placeholders
    vb_x = math.floor(bounds.x0 - MARGIN)
    vb_y = math.floor(bounds.y0 - MARGIN)
    vb_w = math.ceil(bounds.x1 - bounds.x0 + 2 * MARGIN)
    vb_h = math.ceil(bounds.y1 - bounds.y0 + 2 * MARGIN)
    svg = "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="{vb_x} {vb_y} {vb_w} {vb_h}" width="{vb_w}" height="{vb_h}" '
            f'font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', sans-serif" '
            f'font-size="{fmt(BASE_FONT_PX)}">',
            _defs(caps),
            _STYLE,
            *node_parts,
            *edge_parts,
            "</svg>",
        ]
    )
    return svg, placeholders


def _defs(caps: set[tuple[str, int]]) -> str:
    return "<defs>" + "".join(_marker(name, size) for name, size in sorted(caps)) + "</defs>"


def _marker_id(cap: str, stroke_size: int) -> str:
    return f"cap-{cap}-{stroke_size}"


def _marker(name: str, stroke_size: int) -> str:
    """One line end, in board units, with its origin where the drawn line
    stops: the true end, or the start of the stretch hidden under it."""
    head = ARROWHEADS[name]
    k = ARROWHEAD_SCALE.get(stroke_size, 1.0)
    w, h = head.size[0] * k, head.size[1] * k
    shift = head.offset * k * (-1 if head.line_visible else 1)

    def at(x: float, y: float) -> str:
        return f"{fmt(shift + (x - 0.5) * w)} {fmt(y * h)}"

    parts = [f"M {at(*line[0])} " + " ".join(f"L {at(*q)}" for q in line[1:]) for line in head.lines]
    parts += [f"M {at(*poly[0])} " + " ".join(f"L {at(*q)}" for q in poly[1:]) + " Z" for poly in head.polygons]
    for cx, cy, rx, ry in head.ellipses:
        r = f"{fmt(rx * w)} {fmt(ry * h)} 0 1 0"
        parts.append(f"M {at(cx + rx, cy)} A {r} {at(cx - rx, cy)} A {r} {at(cx + rx, cy)} Z")
    fill = "context-stroke" if head.filled else "none"
    return (
        f'<marker id="{_marker_id(name, stroke_size)}" markerUnits="userSpaceOnUse" viewBox="-48 -24 96 48" '
        f'refX="0" refY="0" markerWidth="96" markerHeight="48" orient="auto-start-reverse" overflow="visible">'
        f'<path d="{" ".join(parts)}" fill="{fill}" stroke="context-stroke" '
        f'stroke-width="{fmt(thickness(stroke_size))}" stroke-linejoin="round" stroke-linecap="round"/>'
        "</marker>"
    )


# .node-text fills its foreignObject and clips; .node-body is centred with
# auto margins, which (unlike justify-content:center) collapse to zero when
# content overflows, so overflow always grows downward and stays measurable.
_STYLE = (
    "<style>"
    ".node-text{display:flex;flex-direction:column;margin:0;padding:8px 12px;"
    "color:#172B4D;line-height:1.35;height:100%;width:100%;"
    "box-sizing:border-box;overflow:hidden;}"
    ".node-free{display:block;height:auto;width:auto;padding:0;overflow:visible;}"
    ".node-body{margin:0;overflow-wrap:anywhere;}"
    ".va-top{margin-bottom:auto;}"
    ".va-middle{margin-top:auto;margin-bottom:auto;}"
    ".va-bottom{margin-top:auto;}"
    ".node-body p{margin:0 0 2px 0;}"
    ".node-body ul,.node-body ol{margin:1px 0 1px 18px;padding:0;text-align:left;}"
    ".node-body li{margin:0;}"
    ".node-body li>p{margin:0;}"
    ".node-body strong{font-weight:600;}"
    "</style>"
)


def _render_node(node: Node, box: Box, smap: dict[int, int]) -> str:
    if node.kind is Kind.SHAPE:
        inner = _shape(node, box, smap)
    elif node.kind is Kind.TEXT:
        inner = _free_text(node, box)
    elif node.kind is Kind.IMAGE and node.image:
        x, y, w, h = box
        href = _xml_escape(node.image.href)
        inner = f'<image x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" href="{href}"/>'
    elif node.kind is Kind.LINE and len(node.points) == 2:
        (x1, y1), (x2, y2) = node.points
        width = thickness(int(node.stroke_width))
        return (
            f'<line x1="{fmt(x1)}" y1="{fmt(y1)}" x2="{fmt(x2)}" y2="{fmt(y2)}" '
            f'{_stroke(node.stroke_style, _hex(node.stroke, EDGE_DEFAULT_STROKE), width)}/>'
        )
    else:
        return ""
    if not inner:
        return ""
    x, y, w, h = box
    cls = "wb-node wb-text" if node.kind is Kind.TEXT else "wb-node"
    kind = ""
    if node.kind is Kind.SHAPE:
        kind = f' data-kind="{resolve(node.shape_kind, smap)}"'
    return (
        f'<g class="{cls}" data-id="{_xml_escape(node.id)}"{kind} '
        f'data-x="{fmt(x)}" data-y="{fmt(y)}" data-w="{fmt(w)}" data-h="{fmt(h)}">'
        f"{inner}</g>"
    )


def _shape(node: Node, box: Box, smap: dict[int, int]) -> str:
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return ""
    shape = drawing(node.shape_kind, x, y, w, h, smap)
    width = max(1.0, node.stroke_width) * 1.5
    colours = {
        "fill": node.fill.hex if node.fill else "transparent",
        "stroke": _hex(node.stroke, DEFAULT_STROKE),
    }
    if shape.is_rect:
        parts = [
            f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" '
            f'fill="{colours["fill"]}" {_stroke(node.stroke_style, colours["stroke"], width)}/>'
        ]
    else:
        parts = [_section(i, sec, colours, node.stroke_style, width) for i, sec in enumerate(shape.sections)]
    markup = "".join(parts)
    if not node.html or shape.text is None or shape.text[2] <= 0 or shape.text[3] <= 0:
        return markup
    tx, ty, tw, th = shape.text
    return markup + (
        f'<foreignObject x="{fmt(tx)}" y="{fmt(ty)}" width="{fmt(tw)}" height="{fmt(th)}">'
        f'<div xmlns="http://www.w3.org/1999/xhtml" class="node-text" '
        f'style="text-align:{node.align};font-size:{_font_px(node)}px">'
        f'<div class="node-body va-{node.valign}">{node.html}</div></div>'
        "</foreignObject>"
    )


def _section(index: int, sec: Section, colours: dict[str, str], style: str, width: float) -> str:
    colour = colours[sec.colour]
    rule = ' fill-rule="evenodd"' if sec.rule == "evenodd" else ""
    if sec.paint == "fill":
        return f'<path data-part="{index}" d="{sec.d}" fill="{colour}"{rule} stroke="none"/>'
    if colour == "transparent":
        return ""
    return f'<path data-part="{index}" d="{sec.d}" fill="none" {_stroke(style, colour, width)}/>'


def _free_text(node: Node, box: Box) -> str:
    """Coloured text with no outline that is never clipped: in the source
    free text sizes itself to its content."""
    if not node.html:
        return ""
    x, y, w, h = box
    nowrap = "white-space:nowrap;" if node.auto_width else ""
    return (
        f'<foreignObject x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" style="overflow:visible">'
        f'<div xmlns="http://www.w3.org/1999/xhtml" class="node-text node-free" '
        f'style="color:{_hex(node.color, DEFAULT_STROKE)};text-align:{node.align};'
        f'font-size:{_font_px(node)}px;{nowrap}">'
        f'<div class="node-body">{node.html}</div></div>'
        "</foreignObject>"
    )


def _render_edge(edge: Edge, boxes: dict[str, Box], bounds: _Bounds) -> str:
    # The recorded start/end are computed from a stale element size, so they
    # are only a fallback for an end that is not attached to an element.
    src = anchor_point(boxes[edge.source], edge.source_anchor) if edge.source else edge.start
    tgt = anchor_point(boxes[edge.target], edge.target_anchor) if edge.target else edge.end
    if src is None or tgt is None:
        return ""
    start = End(src, anchor_side(edge.source_anchor) if edge.source else None, edge.start_cap)
    end = End(tgt, anchor_side(edge.target_anchor) if edge.target else None, edge.end_cap)
    path = route(edge.routing, start, end, edge.waypoints, edge.stroke_size)
    for p in path.points():
        bounds.point(*p)

    sa, ta = edge.source_anchor, edge.target_anchor
    markers = "".join(
        f' {attr}="url(#{_marker_id(cap, edge.stroke_size)})"'
        for attr, cap in (("marker-start", edge.start_cap), ("marker-end", edge.end_cap))
        if cap in ARROWHEADS
    )
    (s_ext, s_hide), (t_ext, t_hide) = end_stub(edge.start_cap, edge.stroke_size), end_stub(edge.end_cap, edge.stroke_size)
    stroke = _stroke(edge.stroke_style, _hex(edge.color, EDGE_DEFAULT_STROKE), thickness(edge.stroke_size))
    return (
        f'<path class="wb-edge" data-src="{_xml_escape(edge.source or "")}" '
        f'data-tgt="{_xml_escape(edge.target or "")}" '
        f'data-sa="{fmt(sa[0])},{fmt(sa[1])}" data-ta="{fmt(ta[0])},{fmt(ta[1])}" '
        f'data-routing="{edge.routing}" data-wp="{" ".join(f"{fmt(x)},{fmt(y)}" for x, y in edge.waypoints)}" '
        f'data-ends="{fmt(s_ext)},{int(s_hide)},{fmt(t_ext)},{int(t_hide)}" '
        f'd="{path_data(path)}" fill="none" {stroke}{markers}/>'
    )


def path_data(path: Route) -> str:
    parts = [f"M {_xy(path.start)}"]
    for seg in path.segments:
        parts.append(f"L {_xy(seg[0])}" if len(seg) == 1 else "C " + ", ".join(_xy(p) for p in seg))
    return " ".join(parts)


def _stroke(style: str, colour: str, width: float) -> str:
    """Stroke attributes for a stroke style (none, solid, dashed, dotted)."""
    if style == "none":
        return 'stroke="none"'
    attrs = f'stroke="{colour}" stroke-width="{fmt(width)}"'
    if style == "dashed":
        return attrs + f' stroke-dasharray="{fmt(max(4.0, width * 3))},{fmt(max(3.0, width * 2))}"'
    if style == "dotted":  # zero-length dashes with round caps draw dots
        return attrs + f' stroke-dasharray="0,{fmt(width * 2)}" stroke-linecap="round"'
    return attrs


def _xy(p: Point) -> str:
    return f"{fmt(p[0])} {fmt(p[1])}"


def _hex(c: Rgb | None, default: str) -> str:
    return c.hex if c else default


def _font_px(node: Node) -> str:
    return fmt(BASE_FONT_PX * node.font_scale)
