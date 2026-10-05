"""Interactive HTML viewer: the static SVG plus drag, pan and zoom.

Kept separate from svg.py because SVG files that execute script are
routinely stripped or blocked (Obsidian embeds, mail filters), while a
plain HTML page is the normal way to ship an interactive document.
"""

from __future__ import annotations

import json

from .adf import _xml_escape
from .board import Board, Kind
from .connectors import ELBOW_STUB, TENSION
from .shapes import resolve, stretching_commands
from .svg import svg_document, warn_placeholders

_PAGE_CSS = (
    "html,body{margin:0;height:100%;overflow:hidden;background:#fff;}"
    "svg{display:block;width:100%;height:100%;}"
    "g.wb-node{cursor:move;}"
)


def render_html(board: Board, shape_map: dict[int, int] | None = None) -> str:
    title = _xml_escape(board.meta.title or board.meta.boardId)
    svg, placeholders = svg_document(board, shape_map)
    warn_placeholders("html", board, placeholders)
    # Drawings the viewer regrows when text overflows, keyed by data-kind.
    kinds = {resolve(n.shape_kind, shape_map) for n in board.nodes if n.kind is Kind.SHAPE}
    drawings = {k: cmds for k in sorted(kinds) if (cmds := stretching_commands(k))}
    script = (
        _VIEWER_JS.replace("__ELBOW_STUB__", str(ELBOW_STUB))
        .replace("__TENSION__", str(TENSION))
        .replace("__SHAPES__", json.dumps(drawings, separators=(",", ":")))
    )
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
            f"<script>{script}</script>",
            "</body>",
            "</html>",
        ]
    )


_VIEWER_JS = r"""
(function () {
  if (typeof document === 'undefined') return;
  var TENSION = __TENSION__, STUB = __ELBOW_STUB__;
  var SHAPES = __SHAPES__;
  var SIDE_ANGLE = { right: 0, bottom: Math.PI / 2, left: Math.PI, top: -Math.PI / 2 };
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
      nodeMap.set(idx, { group: g, rect: rect, foreignObject: fo, image: img, kind: g.getAttribute('data-kind'), tx: 0, ty: 0 });
      enableDrag(g, idx);
    }
    var edgeEls = svg.querySelectorAll('path.wb-edge');
    for (var j = 0; j < edgeEls.length; j++) {
      var p = edgeEls[j];
      var ends = (p.getAttribute('data-ends') || '0,0,0,0').split(',').map(parseFloat);
      var wp = (p.getAttribute('data-wp') || '').split(' ').filter(Boolean).map(function (xy) {
        return xy.split(',').map(parseFloat);
      });
      edgeList.push({
        path: p,
        srcIdx: p.getAttribute('data-src'),
        tgtIdx: p.getAttribute('data-tgt'),
        routing: p.getAttribute('data-routing') || 'curved',
        sa: parseAnchor(p.getAttribute('data-sa')),
        ta: parseAnchor(p.getAttribute('data-ta')),
        waypoints: wp,
        sEnd: { ext: ends[0], hide: !!ends[1] },
        tEnd: { ext: ends[2], hide: !!ends[3] }
      });
    }
    autoFit();
    redrawAllEdges();
    enablePanZoom(svg);

    function autoFit() {
      // Grow shapes whose text overflows: a plain rect directly, any other
      // drawing by re-resolving its sections for the new height (offsets,
      // such as rounded corners, keep their size). scrollHeight is in the
      // foreignObject's own user units, so no screen-CTM scaling.
      nodeMap.forEach(function (nd) {
        var fo = nd.foreignObject, rect = nd.rect, parts = nd.kind !== null && SHAPES[nd.kind];
        if (!fo || (!rect && !parts)) return;
        var box = fo.querySelector('div.node-text');
        if (!box) return;
        // scrollHeight is the height the text box needs, padding included.
        // (clientHeight is not a safe baseline: it is clamped up to the
        // padding when the box is shorter than that.)
        var foH = parseFloat(fo.getAttribute('height'));
        var delta = box.scrollHeight - foH;
        if (delta <= 1) return;
        var g = nd.group, h = parseFloat(g.getAttribute('data-h')) + delta;
        fo.setAttribute('height', foH + delta);
        if (rect) {
          rect.setAttribute('height', parseFloat(rect.getAttribute('height')) + delta);
        } else {
          var x = parseFloat(g.getAttribute('data-x')), y = parseFloat(g.getAttribute('data-y'));
          var w = parseFloat(g.getAttribute('data-w'));
          g.querySelectorAll('path[data-part]').forEach(function (p) {
            p.setAttribute('d', shapePath(parts[+p.getAttribute('data-part')], x, y, w, h));
          });
        }
        g.setAttribute('data-h', h);
      });
    }
    // Mirrors shapes.path_data: a point is (x fraction, y fraction, x offset, y offset).
    function shapePath(cmds, x, y, w, h) {
      return cmds.map(function (c) {
        return c[0] + c.slice(1).map(function (p) {
          return ' ' + (x + p[0] * w + p[2]) + ' ' + (y + p[1] * h + p[3]);
        }).join('');
      }).join(' ');
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
      var s = { p: [sb.x + sb.w * e.sa.left, sb.y + sb.h * e.sa.top], side: sideOf(e.sa), ext: e.sEnd.ext, hide: e.sEnd.hide };
      var t = { p: [tb.x + tb.w * e.ta.left, tb.y + tb.h * e.ta.top], side: sideOf(e.ta), ext: e.tEnd.ext, hide: e.tEnd.hide };
      e.path.setAttribute('d', pathData(route(e.routing, s, t, e.waypoints)));
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
    // The connector router: a line-for-line port of connectors.py.
    function route(routing, s, t, wps) {
      if (routing === 'straight') return lines(trim([s.p].concat(wps, [t.p]), s, t));
      if (routing === 'dynamic') return dynamic(s, t, wps);
      return curved(s, t, wps);
    }
    function lines(pts) {
      return { start: pts[0], segs: pts.slice(1).map(function (p) { return [p]; }) };
    }
    function dynamic(s, t, wps) {
      var stub = Math.max(STUB, s.ext + 10, t.ext + 10), pts;
      if (wps.length) {
        pts = [s.p];
        var horizontal = outward(s.side, wps[0][0] - s.p[0], wps[0][1] - s.p[1], true)[0] !== 0;
        wps.concat([t.p]).forEach(function (p) {
          var a = pts[pts.length - 1];
          pts.push(horizontal ? [p[0], a[1]] : [a[0], p[1]]);
          pts.push(p);
        });
      } else {
        pts = elbow(s.p, t.p, s.side, t.side, stub);
      }
      return lines(trim(dropCollinear(pts), s, t));
    }
    function curved(s, t, wps) {
      var pts = [s.p].concat(wps, [t.p]), extended = !!(s.ext || t.ext);
      var a0 = endAngle(s.side, pts, false, extended), a1 = endAngle(t.side, pts, true, extended);
      var sp = pts.slice(), lead = s.ext > 0 && a0 !== null, tail = t.ext > 0 && a1 !== null;
      if (lead) sp[0] = add(pts[0], polar(a0, s.ext));
      if (tail) sp[sp.length - 1] = add(pts[pts.length - 1], polar(a1 + Math.PI, t.ext));
      var segs = [], first = pts[0];
      if (lead && !s.hide) segs.push([sp[0]]);
      else if (lead) first = sp[0];
      segs = segs.concat(spline(sp, a0, a1));
      if (tail && !t.hide) segs.push([pts[pts.length - 1]]);
      return { start: first, segs: segs };
    }
    function spline(pts, a0, a1) {
      var n = pts.length;
      if (n < 2) return [];
      var p0 = pts[0], pn = pts[n - 1];
      if (n === 2 && a0 === null && a1 === null) return [[p0, pn, pn]];
      var ct = a0 !== null ? add(p0, scale(polar(a0, 1), dist(p0, pts[1]) * TENSION)) : null;
      var lt = a1 !== null ? sub(pn, scale(polar(a1, 1), dist(pn, pts[n - 2]) * TENSION)) : null;
      var h = [];
      for (var i = 1; i < n - 1; i++) {
        var prev = (i === 1 && ct) ? ct : pts[i - 1], nxt = (i === n - 2 && lt) ? lt : pts[i + 1];
        var tg = unit(add(unit(sub(pts[i], prev)), unit(sub(nxt, pts[i]))));
        h.push(sub(pts[i], scale(tg, dist(pts[i - 1], pts[i]) * TENSION)));
        h.push(add(pts[i], scale(tg, dist(pts[i], pts[i + 1]) * TENSION)));
      }
      function toward(p, q) { return add(p, scale(unit(sub(q, p)), dist(p, q) * TENSION)); }
      if (ct === null && lt !== null) { h.push(lt); h.unshift(toward(p0, h[0])); }
      else { h.unshift(ct !== null ? ct : toward(p0, h[0])); h.push(lt !== null ? lt : toward(pn, h[h.length - 1])); }
      var out = [];
      for (var k = 0; k < n - 1; k++) out.push([h[2 * k], h[2 * k + 1], pts[k + 1]]);
      return out;
    }
    function endAngle(side, pts, atEnd, extended) {
      if (side) return atEnd ? SIDE_ANGLE[side] + Math.PI : SIDE_ANGLE[side];
      if (pts.length > 2) {
        if (!extended) return null;
        var three = atEnd ? pts.slice(-3) : pts.slice(0, 3);
        var segs = spline(three, null, null), seg = segs[atEnd ? segs.length - 1 : 0];
        return bezierAngle(atEnd ? three[1] : three[0], seg[0], seg[1], seg[2], atEnd ? 1 : 0);
      }
      var w = Math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0]);
      return w + 0.68 * Math.sin((w - Math.PI / 2) / 0.5);
    }
    function bezierAngle(s, c1, c2, e, t) {
      var u = 1 - t;
      var dx = 3 * u * u * (c1[0] - s[0]) + 6 * u * t * (c2[0] - c1[0]) + 3 * t * t * (e[0] - c2[0]);
      var dy = 3 * u * u * (c1[1] - s[1]) + 6 * u * t * (c2[1] - c1[1]) + 3 * t * t * (e[1] - c2[1]);
      return Math.atan2(dy, dx);
    }
    function elbow(src, tgt, sSide, tSide, stub) {
      var dx = tgt[0] - src[0], dy = tgt[1] - src[1];
      var su = outward(sSide, dx, dy, true), tu = outward(tSide, dx, dy, false);
      var a = [src[0] + su[0] * stub, src[1] + su[1] * stub], b = [tgt[0] + tu[0] * stub, tgt[1] + tu[1] * stub], mid;
      if (su[0] && tu[0]) { var mx = (a[0] + b[0]) / 2; mid = [[mx, a[1]], [mx, b[1]]]; }
      else if (su[1] && tu[1]) { var my = (a[1] + b[1]) / 2; mid = [[a[0], my], [b[0], my]]; }
      else if (su[0]) mid = [[b[0], a[1]]];
      else mid = [[a[0], b[1]]];
      return dropCollinear([src, a].concat(mid, [b, tgt]));
    }
    function outward(side, dx, dy, leaving) {
      if (side) return [Math.round(Math.cos(SIDE_ANGLE[side])), Math.round(Math.sin(SIDE_ANGLE[side]))];
      var sign = leaving ? 1 : -1;
      if (Math.abs(dx) >= Math.abs(dy)) return [dx >= 0 ? sign : -sign, 0];
      return [0, dy >= 0 ? sign : -sign];
    }
    function trim(pts, s, t) {
      pts = pts.slice();
      if (s.hide && pts.length >= 2) pts[0] = towardBy(pts[0], pts[1], s.ext);
      if (t.hide && pts.length >= 2) pts[pts.length - 1] = towardBy(pts[pts.length - 1], pts[pts.length - 2], t.ext);
      return pts;
    }
    function dropCollinear(pts) {
      var out = [];
      pts.forEach(function (p) {
        var last = out[out.length - 1];
        if (last && close(p[0], last[0]) && close(p[1], last[1])) return;
        if (out.length >= 2) {
          var q = out[out.length - 2];
          var vertical = close(q[0], last[0]) && close(last[0], p[0]) && (last[1] - q[1]) * (p[1] - last[1]) > 0;
          var horizontal = close(q[1], last[1]) && close(last[1], p[1]) && (last[0] - q[0]) * (p[0] - last[0]) > 0;
          if (vertical || horizontal) {
            out[out.length - 1] = p;
            return;
          }
        }
        out.push(p);
      });
      return out;
    }
    function close(a, b) { return Math.abs(a - b) <= 1e-9 * Math.max(Math.abs(a), Math.abs(b)); }
    function add(a, b) { return [a[0] + b[0], a[1] + b[1]]; }
    function sub(a, b) { return [a[0] - b[0], a[1] - b[1]]; }
    function scale(a, k) { return [a[0] * k, a[1] * k]; }
    function dist(a, b) { return Math.hypot(a[0] - b[0], a[1] - b[1]); }
    function unit(a) { var n = Math.hypot(a[0], a[1]); return n ? [a[0] / n, a[1] / n] : [0, 0]; }
    function polar(angle, len) { return [Math.cos(angle) * len, Math.sin(angle) * len]; }
    function towardBy(a, b, d) { return add(a, scale(unit(sub(b, a)), Math.min(d, dist(a, b)))); }
    function pathData(r) {
      var d = 'M ' + r.start[0] + ' ' + r.start[1];
      r.segs.forEach(function (seg) {
        d += seg.length === 1 ? ' L ' + seg[0][0] + ' ' + seg[0][1]
          : ' C ' + seg.map(function (q) { return q[0] + ' ' + q[1]; }).join(', ');
      });
      return d;
    }
    function sideOf(a) {
      var cx = a.left - 0.5, cy = a.top - 0.5;
      if (Math.max(Math.abs(cx), Math.abs(cy)) < 0.05) return null;
      if (Math.abs(cx) >= Math.abs(cy)) return cx > 0 ? 'right' : 'left';
      return cy > 0 ? 'bottom' : 'top';
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
