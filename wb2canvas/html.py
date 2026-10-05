"""Interactive HTML viewer: the static SVG plus drag, pan and zoom.

Kept separate from svg.py because SVG files that execute script are
routinely stripped or blocked (Obsidian embeds, mail filters), while a
plain HTML page is the normal way to ship an interactive document.
"""

from __future__ import annotations

from .adf import _xml_escape
from .board import Board
from .svg import render_svg

_PAGE_CSS = (
    "html,body{margin:0;height:100%;overflow:hidden;background:#fff;}"
    "svg{display:block;width:100%;height:100%;}"
    "g.wb-node{cursor:move;}"
)


def render_html(board: Board, shape_map: dict[int, str] | None = None) -> str:
    title = _xml_escape(board.meta.title or board.meta.boardId)
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
            render_svg(board, shape_map=shape_map),
            f"<script>{_VIEWER_JS}</script>",
            "</body>",
            "</html>",
        ]
    )


_VIEWER_JS = r"""
(function () {
  if (typeof document === 'undefined') return;
  function init() {
    var svg = document.querySelector('svg');
    if (!svg) return;
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
        // scrollHeight is the height the text box needs, padding included.
        // (clientHeight is not a safe baseline: it is clamped up to the
        // padding when the box is shorter than that.)
        var foH = parseFloat(fo.getAttribute('height'));
        var delta = box.scrollHeight - foH;
        if (delta <= 1) return;
        fo.setAttribute('height', foH + delta);
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
