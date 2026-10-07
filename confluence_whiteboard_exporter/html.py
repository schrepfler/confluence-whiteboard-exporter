"""Interactive HTML viewer: the static SVG plus drag, pan and zoom.

Kept separate from svg.py because SVG files that execute script are
routinely stripped or blocked (Obsidian embeds, mail filters), while a
plain HTML page is the normal way to ship an interactive document.
"""

from __future__ import annotations

import json

from .adf import _xml_escape
from .board import Board, Kind
from .connectors import BEND_RADIUS, ELBOW_STUB, TENSION
from .shapes import (
    CORNER_PHASE,
    CORNER_SPAN,
    DASH_PERIOD,
    EDGE_EXTRA,
    RUN_DASH,
    RUN_PHASE,
    SHAPE_LINE_WIDTH,
    TEXTURE_WINDOW,
    resolve,
    stretching_commands,
)
from .svg import LABEL_GAP, svg_document, warn_placeholders

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
        .replace("__BEND_RADIUS__", str(BEND_RADIUS))
        .replace("__SHAPE_LINE_WIDTH__", str(SHAPE_LINE_WIDTH))
        .replace("__LABEL_GAP__", str(LABEL_GAP))
        .replace("__TENSION__", str(TENSION))
        .replace("__SHAPES__", json.dumps(drawings, separators=(",", ":")))
        .replace("__DASH__", json.dumps({
            "period": DASH_PERIOD, "window": TEXTURE_WINDOW, "cornerPhase": CORNER_PHASE,
            "cornerSpan": CORNER_SPAN, "edgeExtra": EDGE_EXTRA, "runPhase": RUN_PHASE, "runDash": RUN_DASH,
        }))
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
  var TENSION = __TENSION__, STUB = __ELBOW_STUB__, BEND_RADIUS = __BEND_RADIUS__;
  var SHAPES = __SHAPES__, DASH = __DASH__, SHAPE_LINE_WIDTH = __SHAPE_LINE_WIDTH__, LABEL_GAP = __LABEL_GAP__;
  var CORNER_COS = Math.cos(10 * Math.PI / 180);
  var SIDE_ANGLE = { right: 0, bottom: Math.PI / 2, left: Math.PI, top: -Math.PI / 2 };
  var SIDE_DIR = { right: '+x', left: '-x', bottom: '+y', top: '-y' };
  var OPPOSITE = { '+x': '-x', '-x': '+x', '+y': '-y', '-y': '+y' };
  function init() {
    var svg = document.querySelector('svg');
    if (!svg) return;
    var nodeMap = new Map();
    var edgeList = [];
    var nodeEls = svg.querySelectorAll('g.wb-node');
    for (var i = 0; i < nodeEls.length; i++) {
      var g = nodeEls[i];
      var idx = g.getAttribute('data-id');
      var fo = g.querySelector('foreignObject');
      var img = g.querySelector('image');
      nodeMap.set(idx, { group: g, foreignObject: fo, image: img, kind: g.getAttribute('data-kind'), tx: 0, ty: 0 });
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
        axes: (p.getAttribute('data-axes') || '').split(' ').filter(Boolean).map(function (a) { return a === '-' ? null : a; }),
        stroke: parseFloat(p.getAttribute('stroke-width')) || 2,
        labels: [],
        sEnd: { ext: ends[0], hide: !!ends[1] },
        tEnd: { ext: ends[2], hide: !!ends[3] }
      });
    }
    // Labels ride along their connector, at the same proportion of it.
    var edgeById = new Map(edgeList.map(function (e) { return [e.path.getAttribute('data-id'), e]; }));
    svg.querySelectorAll('g.wb-label').forEach(function (g) {
      var e = edgeById.get(g.getAttribute('data-edge')), fo = g.querySelector('foreignObject');
      if (!e || !fo) return;
      var num = function (a) { return parseFloat(fo.getAttribute(a)); };
      e.labels.push({ group: g, p: parseFloat(g.getAttribute('data-p')), side: g.getAttribute('data-side') || 'centre',
                      w: num('width'), h: num('height'), cx: num('x') + num('width') / 2, cy: num('y') + num('height') / 2 });
    });
    autoFit();
    redrawAllEdges();
    enablePanZoom(svg);
    // For tests: the routines the viewer shares with the exporter.
    window.whiteboardExporterViewer = { route: route, pathData: pathData, subpaths: subpaths, subpathD: subpathD, dashLayout: dashLayout,
                               pointAt: pointAt, labelCentre: labelCentre };

    function autoFit() {
      // Grow shapes whose text overflows by re-resolving their drawing for the
      // new height (offsets, such as rounded corners, keep their size) and
      // laying their dashes out again. scrollHeight is in the foreignObject's
      // own user units, so no screen-CTM scaling.
      nodeMap.forEach(function (nd) {
        var fo = nd.foreignObject, shape = nd.kind !== null && SHAPES[nd.kind];
        if (!fo || !shape) return;
        var box = fo.querySelector('div.node-text');
        if (!box) return;
        // scrollHeight is the height the text box needs, padding included.
        // (clientHeight is not a safe baseline: it is clamped up to the
        // padding when the box is shorter than that.)
        var foH = parseFloat(fo.getAttribute('height'));
        var delta = box.scrollHeight - foH;
        if (delta <= 1) return;
        var g = nd.group, h = parseFloat(g.getAttribute('data-h')) + delta;
        var x = parseFloat(g.getAttribute('data-x')), y = parseFloat(g.getAttribute('data-y'));
        var w = parseFloat(g.getAttribute('data-w'));
        fo.setAttribute('height', foH + delta);
        g.querySelectorAll('path[data-part]').forEach(function (p) {
          // The outline is drawn inside the box, as in svg.py.
          var inset = SHAPE_LINE_WIDTH / 2;
          var subs = subpaths(shape.parts[+p.getAttribute('data-part')], x + inset, y + inset, w - 2 * inset, h - 2 * inset, shape.dashMode);
          if (!p.hasAttribute('data-sub')) {
            p.setAttribute('d', subs.map(subpathD).join(' '));
            return;
          }
          var sub = subs[+p.getAttribute('data-sub')];
          p.setAttribute('d', subpathD(sub));
          if (p.hasAttribute('stroke-dasharray')) {
            var layout = dashLayout(sub, parseFloat(p.getAttribute('stroke-width')), shape.dashMode);
            if (layout.dashes.length) {
              p.setAttribute('stroke-dasharray', layout.dashes.join(','));
              p.setAttribute('stroke-dashoffset', layout.offset);
            } else {
              p.removeAttribute('stroke-dasharray');
              p.removeAttribute('stroke-dashoffset');
            }
          }
        });
        g.setAttribute('data-h', h);
      });
    }

    // Shape outlines: a line-for-line port of shapes.py. A point is
    // (x fraction, y fraction, x offset, y offset).
    function subpaths(cmds, x, y, w, h, mode) {
      var out = [], segs = [], cur = null;
      function at(q) { return [x + q[0] * w + q[2], y + q[1] * h + q[3]]; }
      function flush(closed) {
        if (segs.length) out.push({ segments: closed ? reorder(segs, mode || 'runs') : segs, closed: closed });
        segs = [];
      }
      cmds.forEach(function (c) {
        if (c[0] === 'M') { flush(false); cur = at(c[1]); }
        else if (c[0] === 'Z') flush(true);
        else {
          var ends = c.slice(1).map(at);
          segs.push([cur].concat(ends));
          cur = ends[ends.length - 1];
        }
      });
      flush(false);
      return out;
    }
    function subpathD(sub) {
      if (!sub.segments.length) return '';
      var d = 'M ' + sub.segments[0][0][0] + ' ' + sub.segments[0][0][1];
      sub.segments.forEach(function (seg) {
        d += (seg.length === 2 ? ' L ' : ' C ') + seg.slice(1).map(function (q) { return q[0] + ' ' + q[1]; }).join(' ');
      });
      return d + (sub.closed ? ' Z' : '');
    }
    function dashLayout(sub, width, mode) {
      var period = DASH.period * width, lengths = sub.segments.map(segLength);
      var total = lengths.reduce(function (a, b) { return a + b; }, 0), spans = [], s = 0, i, phase, step;
      if (mode.indexOf('even') === 0) {
        var n = Math.floor(total / period);
        if (n === 0) return { dashes: [], offset: 0 };
        lengths.forEach(function (len) { spans.push([s, len, s * n / total, (s + len) * n / total, DASH.window]); s += len; });
      } else if (mode === 'corners') {
        phase = sub.segments[0].length === 4 ? DASH.cornerPhase : DASH.cornerPhase + DASH.cornerSpan;
        sub.segments.forEach(function (seg, k) {
          step = seg.length === 4 ? DASH.cornerSpan : Math.floor(lengths[k] / period) + DASH.edgeExtra;
          spans.push([s, lengths[k], phase, phase + step, DASH.window]);
          s += lengths[k]; phase += step;
        });
      } else {
        i = 0;
        runs(sub).forEach(function (run) {
          var runLengths = lengths.slice(i, i + run.length);
          i += run.length;
          var runTotal = runLengths.reduce(function (a, b) { return a + b; }, 0), n = Math.floor(runTotal / period);
          var cap = n ? width / 2 / (runTotal / n) : 0, win = n ? [-cap, DASH.runDash + cap] : null;
          phase = DASH.runPhase;
          runLengths.forEach(function (len) {
            step = runTotal ? len * n / runTotal : 0;
            spans.push([s, len, phase, phase + step, win]);
            s += len; phase += step;
          });
        });
      }
      var on = [];
      spans.forEach(function (sp) {
        if (sp[1] <= 0) return;
        var pieces = sp[4] === null ? [[sp[0], sp[0] + sp[1]]] : visible(sp[0], sp[1], sp[2], sp[3], sp[4]);
        pieces.forEach(function (pc) {
          var last = on[on.length - 1];
          if (last && pc[0] - last[1] < 1e-6) last[1] = pc[1]; else on.push([pc[0], pc[1]]);
        });
      });
      if (!on.length || (on.length === 1 && on[0][0] < 1e-6 && on[0][1] > total - 1e-6)) return { dashes: [], offset: 0 };
      var cores = on.map(function (o) {
        if (o[1] - o[0] > width) return [o[0] + width / 2, o[1] - width / 2];
        var mid = (o[0] + o[1]) / 2;
        return [mid - 0.005, mid + 0.005];
      });
      var dashes = [];
      cores.forEach(function (c, k) {
        dashes.push(c[1] - c[0]);
        dashes.push(k + 1 < cores.length ? cores[k + 1][0] - c[1] : total + width);
      });
      return { dashes: dashes, offset: -cores[0][0] };
    }
    function visible(start, len, p0, p1, win) {
      var a = win[0], b = win[1], rate = (p1 - p0) / len, out = [];
      for (var k = Math.floor(p0 - b); k <= Math.ceil(p1 - a); k++) {
        var lo = Math.max(p0, k + a), hi = Math.min(p1, k + b);
        if (hi > lo) out.push([start + (lo - p0) / rate, start + (hi - p0) / rate]);
      }
      return out;
    }
    function reorder(segs, mode) {
      if (mode.indexOf('even') === 0) {
        if (signedArea(segs) < 0) segs = segs.slice().reverse().map(function (seg) { return seg.slice().reverse(); });
        var best = 0;
        for (var i = 1; i < segs.length; i++) {
          var p = segs[i][0], q = segs[best][0];
          var better = mode === 'even-left' ? (p[0] < q[0] || (p[0] === q[0] && p[1] < q[1]))
                                            : (-p[0] < -q[0] || (p[0] === q[0] && p[1] < q[1]));
          if (better) best = i;
        }
        return segs.slice(best).concat(segs.slice(0, best));
      }
      for (var j = 0; j < segs.length; j++) {
        if (isCorner(segs[(j + segs.length - 1) % segs.length], segs[j])) return segs.slice(j).concat(segs.slice(0, j));
      }
      return segs;
    }
    function signedArea(segs) {
      var area = 0;
      segs.forEach(function (seg, k) {
        var a = seg[0], b = segs[(k + 1) % segs.length][0];
        area += a[0] * b[1] - b[0] * a[1];
      });
      return area;
    }
    function runs(sub) {
      var out = [];
      sub.segments.forEach(function (seg) {
        var last = out[out.length - 1];
        if (last && !isCorner(last[last.length - 1], seg)) last.push(seg); else out.push([seg]);
      });
      return out;
    }
    function isCorner(a, b) {
      var ta = unit(endTangent(a)), tb = unit(startTangent(b));
      return ta[0] * tb[0] + ta[1] * tb[1] < CORNER_COS;
    }
    function startTangent(seg) {
      for (var i = 1; i < seg.length; i++) {
        if (seg[i][0] !== seg[0][0] || seg[i][1] !== seg[0][1]) return [seg[i][0] - seg[0][0], seg[i][1] - seg[0][1]];
      }
      return [0, 0];
    }
    function endTangent(seg) {
      var e = seg[seg.length - 1];
      for (var i = seg.length - 2; i >= 0; i--) {
        if (seg[i][0] !== e[0] || seg[i][1] !== e[1]) return [e[0] - seg[i][0], e[1] - seg[i][1]];
      }
      return [0, 0];
    }
    function segLength(seg) {
      if (seg.length === 2) return dist(seg[0], seg[1]);
      var total = 0, prev = seg[0];
      for (var i = 1; i <= 16; i++) {
        var t = i / 16, u = 1 - t;
        var q = [0, 1].map(function (k) {
          return u * u * u * seg[0][k] + 3 * u * u * t * seg[1][k] + 3 * u * t * t * seg[2][k] + t * t * t * seg[3][k];
        });
        total += dist(prev, q);
        prev = q;
      }
      return total;
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
      var s = { p: [sb.x + sb.w * e.sa.left, sb.y + sb.h * e.sa.top], side: sideOf(e.sa), ext: e.sEnd.ext, hide: e.sEnd.hide,
                box: [sb.x, sb.y, sb.w, sb.h] };
      var t = { p: [tb.x + tb.w * e.ta.left, tb.y + tb.h * e.ta.top], side: sideOf(e.ta), ext: e.tEnd.ext, hide: e.tEnd.hide,
                box: [tb.x, tb.y, tb.w, tb.h] };
      var r = route(e.routing, s, t, e.waypoints, e.axes, e.stroke);
      e.path.setAttribute('d', pathData(r));
      e.labels.forEach(function (lb) {
        var c = labelCentre(pointAt(r, lb.p), lb.side, lb.w, lb.h);
        lb.group.setAttribute('transform', 'translate(' + (c[0] - lb.cx) + ' ' + (c[1] - lb.cy) + ')');
      });
    }
    // The point a label sits at and the path's direction there, as
    // connectors.point_at finds them: the segment by length, then that share
    // of the segment's curve parameter.
    function pointAt(r, p) {
      var pieces = [], cur = r.start;
      r.segs.forEach(function (seg) { pieces.push([cur].concat(seg)); cur = seg[seg.length - 1]; });
      var lengths = pieces.map(segLength);
      var remaining = Math.max(0, Math.min(1, p)) * lengths.reduce(function (a, b) { return a + b; }, 0);
      for (var i = 0; i < pieces.length; i++) {
        if (remaining > lengths[i] && i < pieces.length - 1) { remaining -= lengths[i]; continue; }
        var q = pieces[i], t = lengths[i] ? Math.min(remaining / lengths[i], 1) : 0, u = 1 - t;
        if (q.length === 2) {
          return { p: [q[0][0] + (q[1][0] - q[0][0]) * t, q[0][1] + (q[1][1] - q[0][1]) * t],
                   angle: Math.atan2(q[1][1] - q[0][1], q[1][0] - q[0][0]) };
        }
        var at = function (k) { return u * u * u * q[0][k] + 3 * u * u * t * q[1][k] + 3 * u * t * t * q[2][k] + t * t * t * q[3][k]; };
        var dk = function (k) { return 3 * u * u * (q[1][k] - q[0][k]) + 6 * u * t * (q[2][k] - q[1][k]) + 3 * t * t * (q[3][k] - q[2][k]); };
        return { p: [at(0), at(1)], angle: Math.atan2(dk(1), dk(0)) };
      }
      return { p: r.start, angle: 0 };
    }
    // Where a label's box is centred: on its point, or beside the line with
    // the corner nearest it LABEL_GAP away, as svg._render_label places it.
    function labelCentre(pt, side, w, h) {
      if (side !== 'left' && side !== 'right') return pt.p;
      var sign = side === 'left' ? 1 : -1, nx = Math.sin(pt.angle) * sign, ny = -Math.cos(pt.angle) * sign;
      var sgn = function (v) { return Math.abs(v) < 1e-9 ? 0 : v > 0 ? 1 : -1; };
      return [pt.p[0] + nx * LABEL_GAP + sgn(nx) * w / 2, pt.p[1] + ny * LABEL_GAP + sgn(ny) * h / 2];
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
    function route(routing, s, t, wps, axes, stroke) {
      if (routing === 'straight') return lines(trim([s.p].concat(wps, [t.p]), s, t));
      if (routing === 'dynamic') return dynamic(s, t, wps, axes || [], stroke || 2);
      return curved(s, t, wps);
    }
    function lines(pts) {
      return { start: pts[0], segs: pts.slice(1).map(function (p) { return [p]; }) };
    }
    function dynamic(s, t, wps, axes, stroke) {
      var pts;
      var handles = wps.length && axes.length === wps.length && axes.every(function (a) { return a === 'x' || a === 'y'; });
      if (handles) {
        pts = throughHandles(s, t, wps, axes, stroke);
      } else {
        pts = elbow(s.p, t.p, s.side, t.side, Math.max(STUB, s.ext + 10, t.ext + 10), 4 * stroke + 10);
      }
      return rounded(trim(dropCollinear(pts), s, t), BEND_RADIUS);
    }
    function throughHandles(s, t, wps, axes, stroke) {
      var margin = 4 * stroke + 10;
      function edge(box, dir) {
        return { '+x': box[0] + box[2] + margin, '-x': box[0] - margin, '+y': box[1] + box[3] + margin, '-y': box[1] - margin }[dir];
      }
      var sDir = s.box && s.side ? SIDE_DIR[s.side] : null;
      var tDir = t.box && t.side ? OPPOSITE[SIDE_DIR[t.side]] : null;
      var xFirst = sDir ? sDir[1] === 'x' : axes[0] === 'x';
      var vals = xFirst ? [s.p[0], s.p[1]] : [s.p[1], s.p[0]];
      function misfit(movesX) { return movesX !== ((vals.length % 2 === 0) === xFirst); }
      if (sDir && misfit(axes[0] === 'x')) vals.push(edge(s.box, sDir));
      wps.forEach(function (p, k) {
        if (misfit(axes[k] === 'x')) vals.push(null);
        vals.push(axes[k] === 'x' ? p[0] : p[1]);
      });
      var xLast = !(tDir ? tDir[1] === 'x' : axes[axes.length - 1] === 'x');
      if (misfit(xLast)) {
        if (tDir) vals.push(edge(t.box, OPPOSITE[tDir])); else xLast = !xLast;
      }
      vals = vals.concat(xLast ? [t.p[0], t.p[1]] : [t.p[1], t.p[0]]);
      fillGaps(vals, 0); fillGaps(vals, 1);
      var pts = [s.p];
      for (var i = 2; i < vals.length; i++) {
        var prev = vals[i - 1], cur = vals[i];
        pts.push(((i % 2 === 0) === xFirst) ? [cur, prev] : [prev, cur]);
      }
      return pts.filter(function (p, k) { return k === 0 || p[0] !== pts[k - 1][0] || p[1] !== pts[k - 1][1]; });
    }
    function fillGaps(vals, parity) {
      var run = 0, last = 0;
      for (var i = parity; i < vals.length; i += 2) {
        var v = vals[i];
        if (v === null) run++;
        else if (run) {
          for (var n = 0; n < run; n++) vals[i - 2 * n - 2] = v + (last - v) * (n + 1) / (run + 1);
          run = 0;
        } else last = v;
      }
    }
    function rounded(pts, radius) {
      var k = 0.5523, segs = [];
      for (var i = 1; i < pts.length - 1; i++) {
        var a = pts[i - 1], p = pts[i], b = pts[i + 1];
        var din = unit(sub(p, a)), dout = unit(sub(b, p));
        if (Math.abs(din[0] * dout[0] + din[1] * dout[1]) > 0.999) { segs.push([p]); continue; }
        var r = Math.min(radius, dist(a, p) / 2, dist(p, b) / 2);
        var pin = sub(p, scale(din, r)), pout = add(p, scale(dout, r));
        segs.push([pin]);
        segs.push([add(pin, scale(din, r * k)), sub(pout, scale(dout, r * k)), pout]);
      }
      segs.push([pts[pts.length - 1]]);
      return { start: pts[0], segs: segs };
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
    function elbow(src, tgt, sSide, tSide, stub, margin) {
      var dx = tgt[0] - src[0], dy = tgt[1] - src[1];
      var su = outward(sSide, dx, dy, true), tu = outward(tSide, dx, dy, false);
      var a = [src[0] + su[0] * stub, src[1] + su[1] * stub], b = [tgt[0] + tu[0] * stub, tgt[1] + tu[1] * stub], mid;
      var same = su[0] === tu[0] && su[1] === tu[1];
      if (same && su[0]) {
        var x = su[0] > 0 ? Math.max(src[0], tgt[0]) + margin : Math.min(src[0], tgt[0]) - margin;
        return dropCollinear([src, [x, src[1]], [x, tgt[1]], tgt]);
      }
      if (same && su[1]) {
        var y = su[1] > 0 ? Math.max(src[1], tgt[1]) + margin : Math.min(src[1], tgt[1]) - margin;
        return dropCollinear([src, [src[0], y], [tgt[0], y], tgt]);
      }
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
