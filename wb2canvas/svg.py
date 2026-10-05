"""SVG renderer: a visual replica of the whiteboard.

Unlike JSON Canvas, SVG can show what Confluence draws: dashed outlines,
shape stereotypes, coloured free text, curved connectors and dividers.
Image hrefs are relative to the SVG file, so it must sit next to `media/`.
"""

from __future__ import annotations

import math

from .adf import _xml_escape
from .board import SVG_METRICS, Board, Box, Edge, Kind, Node, Point, Rgb, anchor_point, anchor_side, layout
from .shapes import DEFAULT_SHAPE_MAP, fmt, outline, text_inset

DEFAULT_STROKE = "#172B4D"
EDGE_DEFAULT_STROKE = "#758195"
MARGIN = 60
RX = 8
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


def render_svg(board: Board, shape_map: dict[int, str] | None = None) -> str:
    smap = {**DEFAULT_SHAPE_MAP, **(shape_map or {})}
    boxes = layout(board, SVG_METRICS)
    bounds = _Bounds()

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

    if bounds.empty:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100"></svg>'
    vb_x = math.floor(bounds.x0 - MARGIN)
    vb_y = math.floor(bounds.y0 - MARGIN)
    vb_w = math.ceil(bounds.x1 - bounds.x0 + 2 * MARGIN)
    vb_h = math.ceil(bounds.y1 - bounds.y0 + 2 * MARGIN)
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="{vb_x} {vb_y} {vb_w} {vb_h}" width="{vb_w}" height="{vb_h}" '
            f'font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', sans-serif" '
            f'font-size="{fmt(BASE_FONT_PX)}">',
            _DEFS,
            _STYLE,
            *node_parts,
            *edge_parts,
            _interactive_script(),
            "</svg>",
        ]
    )


_DEFS = (
    "<defs>"
    '<marker id="arrow" viewBox="0 0 12 12" refX="11" refY="6" '
    'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
    '<path d="M 0 0 L 12 6 L 0 12 z" fill="context-stroke" />'
    "</marker>"
    "</defs>"
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


def _render_node(node: Node, box: Box, smap: dict[int, str]) -> str:
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
        width = max(1.0, node.stroke_width) * 1.5
        return (
            f'<line x1="{fmt(x1)}" y1="{fmt(y1)}" x2="{fmt(x2)}" y2="{fmt(y2)}" '
            f'stroke="{_hex(node.stroke, EDGE_DEFAULT_STROKE)}" stroke-width="{fmt(width)}" '
            f"{_dash(node.dashed, width)}/>"
        )
    else:
        return ""
    if not inner:
        return ""
    x, y, w, h = box
    cls = "wb-node wb-text" if node.kind is Kind.TEXT else "wb-node"
    return (
        f'<g class="{cls}" data-id="{_xml_escape(node.id)}" '
        f'data-x="{fmt(x)}" data-y="{fmt(y)}" data-w="{fmt(w)}" data-h="{fmt(h)}">'
        f"{inner}</g>"
    )


def _shape(node: Node, box: Box, smap: dict[int, str]) -> str:
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return ""
    fill = node.fill.hex if node.fill else "transparent"
    stroke = _hex(node.stroke, DEFAULT_STROKE)
    stroke_w = max(1.0, node.stroke_width) * 1.5
    dash = _dash(node.dashed, stroke_w)
    d, name = outline(node.shape_kind, x, y, w, h, smap)
    if name == "rect":
        shape = (
            f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" '
            f'rx="{RX}" ry="{RX}" fill="{fill}" stroke="{stroke}" stroke-width="{fmt(stroke_w)}" {dash}/>'
        )
    else:
        shape = f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{fmt(stroke_w)}" {dash}/>'

    top, right, bottom, left = text_inset(name, w, h)
    tw, th = w - left - right, h - top - bottom
    if not node.html or tw <= 0 or th <= 0:
        return shape
    return shape + (
        f'<foreignObject x="{fmt(x + left)}" y="{fmt(y + top)}" width="{fmt(tw)}" height="{fmt(th)}">'
        f'<div xmlns="http://www.w3.org/1999/xhtml" class="node-text" '
        f'style="text-align:{node.align};font-size:{_font_px(node)}px">'
        f'<div class="node-body va-{node.valign}">{node.html}</div></div>'
        "</foreignObject>"
    )


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
    # The recorded start/end are sometimes internal control points rather
    # than edge anchors, so they are only a fallback for a missing end.
    src = anchor_point(boxes[edge.source], edge.source_anchor) if edge.source else edge.start
    tgt = anchor_point(boxes[edge.target], edge.target_anchor) if edge.target else edge.end
    if src is None or tgt is None:
        return ""
    pts = curve_points(src, tgt, anchor_side(edge.source_anchor), anchor_side(edge.target_anchor))
    for p in pts:  # a cubic Bezier lies inside the hull of its control points
        bounds.point(*p)
    (sx, sy), (c1x, c1y), (c2x, c2y), (ex, ey) = pts
    d = f"M {fmt(sx)} {fmt(sy)} C {fmt(c1x)} {fmt(c1y)}, {fmt(c2x)} {fmt(c2y)}, {fmt(ex)} {fmt(ey)}"

    width = max(1.0, edge.width) * 1.5
    sa, ta = edge.source_anchor, edge.target_anchor
    markers = (' marker-start="url(#arrow)"' if edge.start_arrow else "") + (
        ' marker-end="url(#arrow)"' if edge.end_arrow else ""
    )
    return (
        f'<path class="wb-edge" data-src="{_xml_escape(edge.source or "")}" '
        f'data-tgt="{_xml_escape(edge.target or "")}" '
        f'data-sa="{fmt(sa[0])},{fmt(sa[1])}" data-ta="{fmt(ta[0])},{fmt(ta[1])}" '
        f'd="{d}" stroke="{_hex(edge.color, EDGE_DEFAULT_STROKE)}" stroke-width="{fmt(width)}" '
        f'fill="none" {_dash(edge.dashed, width)}{markers}/>'
    )


def curve_points(
    src: Point, tgt: Point, s_side: str | None, t_side: str | None
) -> tuple[Point, Point, Point, Point]:
    """Cubic Bezier from src to tgt whose ends leave/enter perpendicular to
    the anchored box edges, so arrowheads point straight at the box."""
    dx, dy = tgt[0] - src[0], tgt[1] - src[1]
    handle = max(40.0, max(abs(dx), abs(dy)) * 0.4)
    return src, _handle(src, s_side, handle, dx, dy, True), _handle(tgt, t_side, handle, dx, dy, False), tgt


def _handle(p: Point, side: str | None, h: float, dx: float, dy: float, leaving: bool) -> Point:
    x, y = p
    if side == "right":
        return x + h, y
    if side == "left":
        return x - h, y
    if side == "top":
        return x, y - h
    if side == "bottom":
        return x, y + h
    if abs(dx) >= abs(dy):
        return (x + h if leaving else x - h), y
    return x, (y + h if leaving else y - h)


def _dash(dashed: bool, stroke_w: float) -> str:
    if not dashed:
        return ""
    return f'stroke-dasharray="{fmt(max(4.0, stroke_w * 3))},{fmt(max(3.0, stroke_w * 2))}" '


def _hex(c: Rgb | None, default: str) -> str:
    return c.hex if c else default


def _font_px(node: Node) -> str:
    return fmt(BASE_FONT_PX * node.font_scale)


_INTERACTIVE_JS = r"""
(function () {
  if (typeof document === 'undefined') return;
  function init() {
    var svg = document.documentElement;
    if (!svg || svg.tagName.toLowerCase() !== 'svg') {
      svg = document.querySelector('svg');
      if (!svg) return;
    }
    // Opened directly in a browser: fill the window instead of a fixed-size
    // canvas. (When embedded via <img>, scripts never run and the intrinsic
    // size is kept.)
    if (svg === document.documentElement) {
      svg.setAttribute('width', '100%');
      svg.setAttribute('height', '100%');
    }
    var nodeMap = new Map();
    var edgeList = [];
    var nodeEls = svg.querySelectorAll('g.wb-node');
    for (var i = 0; i < nodeEls.length; i++) {
      var g = nodeEls[i];
      var idx = g.getAttribute('data-id');
      var rect = g.querySelector('rect');
      var fo = g.querySelector('foreignObject');
      var img = g.querySelector('image');
      nodeMap.set(idx, { group: g, rect: rect, foreignObject: fo, image: img, tx: 0, ty: 0 });
      enableDrag(g, idx);
    }
    var edgeEls = svg.querySelectorAll('path.wb-edge');
    for (var j = 0; j < edgeEls.length; j++) {
      var p = edgeEls[j];
      var sa = parseAnchor(p.getAttribute('data-sa'));
      var ta = parseAnchor(p.getAttribute('data-ta'));
      edgeList.push({
        path: p,
        srcIdx: p.getAttribute('data-src'),
        tgtIdx: p.getAttribute('data-tgt'),
        sa: sa, ta: ta
      });
    }
    autoFit();
    redrawAllEdges();
    enablePanZoom(svg);

    function autoFit() {
      // Grow plain-rect nodes whose text overflows. scrollHeight/clientHeight
      // are in the foreignObject's own user units, so no screen-CTM scaling.
      // Non-rect outlines (cylinder, ...) would need their path regenerated.
      nodeMap.forEach(function (nd) {
        var fo = nd.foreignObject, rect = nd.rect;
        if (!fo || !rect) return;
        var box = fo.querySelector('div.node-text');
        if (!box) return;
        var delta = box.scrollHeight - box.clientHeight;
        if (delta <= 1) return;
        fo.setAttribute('height', parseFloat(fo.getAttribute('height')) + delta);
        rect.setAttribute('height', parseFloat(rect.getAttribute('height')) + delta);
        nd.group.setAttribute('data-h', parseFloat(nd.group.getAttribute('data-h')) + delta);
      });
    }

    function enableDrag(g, idx) {
      g.style.cursor = 'move';
      g.addEventListener('mousedown', function (e) {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        var nd = nodeMap.get(idx);
        var startX = e.clientX;
        var startY = e.clientY;
        var origTx = nd.tx;
        var origTy = nd.ty;
        var ctm = svg.getScreenCTM();
        var sx = ctm ? ctm.a : 1;
        var sy = ctm ? ctm.d : 1;
        function move(ev) {
          nd.tx = origTx + (ev.clientX - startX) / sx;
          nd.ty = origTy + (ev.clientY - startY) / sy;
          g.setAttribute('transform', 'translate(' + nd.tx + ' ' + nd.ty + ')');
          redrawEdgesFor(idx);
        }
        function up() {
          window.removeEventListener('mousemove', move);
          window.removeEventListener('mouseup', up);
        }
        window.addEventListener('mousemove', move);
        window.addEventListener('mouseup', up);
      });
    }

    function redrawAllEdges() {
      for (var k = 0; k < edgeList.length; k++) recomputeEdge(edgeList[k]);
    }
    function redrawEdgesFor(idx) {
      for (var k = 0; k < edgeList.length; k++) {
        var e = edgeList[k];
        if (e.srcIdx === idx || e.tgtIdx === idx) recomputeEdge(e);
      }
    }
    function recomputeEdge(e) {
      var sb = bounds(nodeMap.get(e.srcIdx));
      var tb = bounds(nodeMap.get(e.tgtIdx));
      if (!sb || !tb) return;
      var sx = sb.x + sb.w * e.sa.left;
      var sy = sb.y + sb.h * e.sa.top;
      var ex = tb.x + tb.w * e.ta.left;
      var ey = tb.y + tb.h * e.ta.top;
      e.path.setAttribute('d', buildCurve(sx, sy, ex, ey, e.sa, e.ta));
    }
    function bounds(nd) {
      if (!nd) return null;
      var g = nd.group;
      return {
        x: parseFloat(g.getAttribute('data-x')) + nd.tx,
        y: parseFloat(g.getAttribute('data-y')) + nd.ty,
        w: parseFloat(g.getAttribute('data-w')),
        h: parseFloat(g.getAttribute('data-h'))
      };
    }
    function buildCurve(sx, sy, ex, ey, sa, ta) {
      var dx = ex - sx, dy = ey - sy;
      var h = Math.max(40, Math.max(Math.abs(dx), Math.abs(dy)) * 0.4);
      var c1 = ctrl(sx, sy, dirOf(sa), h, dx, dy, true);
      var c2 = ctrl(ex, ey, dirOf(ta), h, dx, dy, false);
      return 'M ' + sx + ' ' + sy + ' C ' + c1.x + ' ' + c1.y + ', ' + c2.x + ' ' + c2.y + ', ' + ex + ' ' + ey;
    }
    function dirOf(a) {
      if (a.left <= 0.05) return 'left';
      if (a.left >= 0.95) return 'right';
      if (a.top <= 0.05) return 'top';
      if (a.top >= 0.95) return 'bottom';
      return null;
    }
    function ctrl(x, y, d, h, dx, dy, leaving) {
      if (d === 'right') return { x: x + h, y: y };
      if (d === 'left') return { x: x - h, y: y };
      if (d === 'top') return { x: x, y: y - h };
      if (d === 'bottom') return { x: x, y: y + h };
      if (Math.abs(dx) >= Math.abs(dy)) return { x: x + (leaving ? h : -h), y: y };
      return { x: x, y: y + (leaving ? h : -h) };
    }
    function parseAnchor(s) {
      if (!s) return { left: 0.5, top: 0.5 };
      var p = s.split(',');
      return { left: parseFloat(p[0]), top: parseFloat(p[1]) };
    }

    function enablePanZoom(svg) {
      var vb = svg.getAttribute('viewBox').split(/\s+/).map(parseFloat);
      var view = { x: vb[0], y: vb[1], w: vb[2], h: vb[3] };
      var panning = null;
      svg.addEventListener('mousedown', function (e) {
        if (e.target.closest('g.wb-node')) return;
        if (e.button !== 0) return;
        panning = { startX: e.clientX, startY: e.clientY, vx: view.x, vy: view.y };
        svg.style.cursor = 'grabbing';
      });
      window.addEventListener('mousemove', function (e) {
        if (!panning) return;
        var ctm = svg.getScreenCTM();
        var sx = ctm ? ctm.a : 1, sy = ctm ? ctm.d : 1;
        view.x = panning.vx - (e.clientX - panning.startX) / sx;
        view.y = panning.vy - (e.clientY - panning.startY) / sy;
        applyView();
      });
      window.addEventListener('mouseup', function () {
        panning = null;
        svg.style.cursor = '';
      });
      svg.addEventListener('wheel', function (e) {
        e.preventDefault();
        var ctm = svg.getScreenCTM();
        var sx = ctm ? ctm.a : 1, sy = ctm ? ctm.d : 1;
        var pt = svg.createSVGPoint();
        pt.x = e.clientX; pt.y = e.clientY;
        var pre = pt.matrixTransform(ctm.inverse());
        var factor = e.deltaY > 0 ? 1.1 : 1 / 1.1;
        view.x = pre.x - (pre.x - view.x) * factor;
        view.y = pre.y - (pre.y - view.y) * factor;
        view.w *= factor; view.h *= factor;
        applyView();
      }, { passive: false });
      function applyView() {
        svg.setAttribute('viewBox', view.x + ' ' + view.y + ' ' + view.w + ' ' + view.h);
      }
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
"""


def _interactive_script() -> str:
    return f'<script type="application/ecmascript"><![CDATA[{_INTERACTIVE_JS}]]></script>'
