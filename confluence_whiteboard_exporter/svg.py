"""SVG renderer: a static visual replica of the whiteboard.

Unlike JSON Canvas, SVG can show what Confluence draws: dashed outlines,
shape stereotypes, coloured free text, curved connectors and dividers.
The output carries no script, so it is safe to embed or share; the
interactive viewer in html.py wraps it. Image hrefs are relative to the
SVG file, so it must sit next to `media/`.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections import Counter

from .adf import _xml_escape
from .board import (
    EDITOR_BOLD_WEIGHT,
    EDITOR_FONT_PX,
    EDITOR_HEADING_ABOVE,
    EDITOR_HEADING_BELOW,
    EDITOR_HEADING_WEIGHTS,
    EDITOR_HEADINGS,
    EDITOR_LINE_PX,
    LIST_INDENT_EM,
    SVG_METRICS,
    Board,
    Box,
    Comment,
    Edge,
    Kind,
    Label,
    Node,
    Point,
    Rgb,
    anchor_point,
    anchor_side,
    layout,
)
from .connectors import ARROWHEAD_SCALE, ARROWHEADS, End, Route, end_stub, point_at, route, thickness
from .palette import drawn
from .shapes import DASH_PERIOD, DASH_SHARE, SHAPE_LINE_WIDTH, Section, dash_layout, drawing, fmt, kind_label, resolve

log = logging.getLogger(__name__)

DEFAULT_STROKE = drawn("#172B4D")  # the editor's default text and outline colour
EDGE_DEFAULT_STROKE = drawn("#758195")
MARGIN = 60
BASE_FONT_PX = EDITOR_FONT_PX  # the editor's text spacing, shared with the layout model
LINE_HEIGHT = EDITOR_LINE_PX / EDITOR_FONT_PX
TEXT_PADDING = 12.0  # round a shape's text, inside its content box; grows with the font scale
# Atlassian Sans has an optical size axis, which browsers set from the font
# size. The editor sets text of every size and scale at one optical size,
# about 18 (fit to its drawings on the reference board).
OPTICAL_SIZE = 18
HEADINGS = EDITOR_HEADINGS
FONT_FAMILY = ('"Atlassian Sans", ui-sans-serif, -apple-system, BlinkMacSystemFont, '
               '"Segoe UI", Ubuntu, "Helvetica Neue", sans-serif')  # the editor's stack
LABEL_PADDING = 4.0  # round a connector label's text, as in the editor
LABEL_GAP = 12.0  # between a side label's box and its line, as in the editor
# A section's title sits on a tab above its top-left corner (the editor's
# section layout): 24 tall, its middle 18 above the top edge, the title 8
# in from its left. The tab is the title's width and 13.6 more, no wider
# than half the section less 8 (the room its action label leaves); a longer
# title ends in an ellipsis. Its border is 3 wide, inside the box, and a
# section with a drop shadow has none. Measured on the reference board.
SECTION_TAB_H, SECTION_TAB_RISE, SECTION_TAB_PAD = 24.0, 18.0, 8.0
SECTION_TAB_EXTRA = 13.6
SECTION_TAB_ROOM = 8.0
SECTION_BORDER = 3.0
SECTION_RADIUS = 4.0
SHADOWS = (
    '<filter id="wb-sticky-shadow" x="-10%" y="-10%" width="120%" height="130%">'
    '<feDropShadow dx="0" dy="1" stdDeviation="1.5" flood-color="#1E1F21" flood-opacity="0.2"/></filter>'
    '<filter id="wb-section-shadow" x="-10%" y="-10%" width="120%" height="130%">'
    '<feDropShadow dx="0" dy="4" stdDeviation="4" flood-color="#1E1F21" flood-opacity="0.15"/></filter>'
)
OUTLINE_INSET = SHAPE_LINE_WIDTH / 2  # how far inside the box a shape's outline is centred


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
        d.kind for n in board.nodes
        if n.kind is Kind.SHAPE and (d := drawing(n.shape_kind, 0, 0, 1, 1, smap, board.drawings)).placeholder
    )

    # Nodes in source z-order (a section covers what came before it, as in
    # the editor); connectors last so arrowheads stay on top.
    node_parts: list[str] = []
    for node in board.nodes:
        box = boxes[node.id]
        if node.kind is Kind.LINE:
            for p in node.points:
                bounds.point(*p)
        else:
            bounds.box(*box)
        if node.kind is Kind.SECTION and (tab := section_tab(node, box)):
            bounds.box(*tab)
        if markup := _render_node(node, box, smap, board):
            node_parts.append(markup)
    edge_parts: list[str] = []
    label_parts: list[str] = []  # above every connector, as in the editor
    for e in board.edges:
        line, labels = _render_edge(e, boxes, bounds)
        edge_parts.append(line)
        label_parts.extend(labels)
    caps = {(c, e.stroke_size) for e in board.edges for c in (e.start_cap, e.end_cap) if c in ARROWHEADS}
    pin = pin_size(bounds, board.comments)
    for c in board.comments:
        bounds.box(c.x - pin / 2, c.y - pin / 2, pin, pin)
    pin_parts = [_comment_pin(c, pin) for c in board.comments]

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
            f"font-family='{FONT_FAMILY}' "
            f'font-size="{fmt(BASE_FONT_PX)}" style="font-variation-settings:&quot;opsz&quot; {OPTICAL_SIZE}">',
            _defs(caps),
            _STYLE,
            *node_parts,
            *edge_parts,
            *label_parts,
            *([_PIN_STYLE] if pin_parts else []),
            *pin_parts,
            "</svg>",
        ]
    )
    return svg, placeholders


# ------------------------------------------------------------ comment pins

# The editor draws a pin the same size on screen at every zoom: its author's
# picture (or initials) in a ring, about 32 px across. Here it is drawn to be that
# size when the board fills a window about 1600 by 1000; the viewer keeps
# it that size as it zooms. Its thread is its tooltip, and a card the
# viewer opens.
PIN = 32.0
PIN_SCREEN = 32.0
_PIN_VIEW = (1600.0, 1000.0)
_PIN_RING, _PIN_RESOLVED = "#1868DB", "#8590A2"
_PIN_COLOURS = ("#1868DB", "#E06C00", "#6E5DC6", "#1F845A", "#C9372C", "#0B6BA0", "#AE4787")
_THREAD_W = 280.0
_CARD_ROOM = 12.0  # round the card, for its shadow
_PIN_STYLE = (
    '<defs><clipPath id="wb-pin-face"><circle r="13"/></clipPath></defs>'
    "<style>"
    "g.wb-comment{cursor:pointer;}"
    f".wb-thread-room{{padding:2px {fmt(_CARD_ROOM)}px {fmt(_CARD_ROOM)}px 2px;}}"
    ".wb-thread-card{box-sizing:border-box;width:100%;padding:8px 10px;background:#fff;border-radius:6px;"
    f"box-shadow:0 4px 12px rgba(30,31,33,.25);font-size:12px;line-height:1.35;color:{DEFAULT_STROKE};}}"
    ".wb-thread-state{font-weight:600;color:#216E4E;margin-bottom:6px;}"
    ".wb-post+.wb-post{border-top:1px solid #DCDFE4;margin-top:6px;padding-top:6px;}"
    ".wb-post-head{color:#626F86;margin-bottom:2px;}.wb-post-head b{color:#172B4D;font-weight:600;}"
    ".wb-post-body p{margin:0;}"
    "</style>"
)


def pin_size(bounds: _Bounds, comments: list[Comment]) -> float:
    """How large to draw comment pins on a board this size."""
    if not comments or bounds.empty:
        return PIN
    fit = max((bounds.x1 - bounds.x0 + 2 * MARGIN) / _PIN_VIEW[0], (bounds.y1 - bounds.y0 + 2 * MARGIN) / _PIN_VIEW[1])
    return max(PIN, PIN_SCREEN * fit)


def _comment_pin(c: Comment, size: float) -> str:
    """A pin, centred on the origin of a 32 square scaled to `size`, with
    its thread as a tooltip and as a hidden card beside it."""
    first = c.posts[0] if c.posts else None
    ring = _PIN_RESOLVED if c.resolved else _PIN_RING
    if first and first.avatar:
        face = (f'<image x="-13" y="-13" width="26" height="26" href="{_xml_escape(first.avatar)}" '
                f'clip-path="url(#wb-pin-face)" preserveAspectRatio="xMidYMid slice"/>')
    else:
        initials = _initials(first.author if first else None)
        face = (f'<circle r="13" fill="{_author_colour(first.author_id if first else None)}"/>'
                f'<text text-anchor="middle" dominant-baseline="central" font-size="{12 if len(initials) < 2 else 10}" '
                f'font-weight="600" fill="#fff">{_xml_escape(initials)}</text>')
    badge = ('<g transform="translate(11 -11)"><circle r="6" fill="#22A06B" stroke="#fff" stroke-width="1.5"/>'
             '<path d="M -2.5 0 L -0.6 2 L 2.6 -1.8" fill="none" stroke="#fff" stroke-width="1.5" '
             'stroke-linecap="round" stroke-linejoin="round"/></g>') if c.resolved else ""
    parent = f' data-parent="{_xml_escape(c.parent)}"' if c.parent else ""
    state = ' data-resolved="true"' if c.resolved else ""
    return (
        f'<g class="wb-comment" data-id="{_xml_escape(c.id)}"{parent}{state} data-x="{fmt(c.x)}" data-y="{fmt(c.y)}" '
        f'data-size="{fmt(size)}" transform="translate({fmt(c.x)} {fmt(c.y)})">'
        f"<title>{_xml_escape(thread_text(c))}</title>"
        f'<g class="wb-pin" transform="scale({fmt(size / PIN)})">'
        f'<g opacity="{0.7 if c.resolved else 1}">'
        f'<circle r="16" fill="{ring}" filter="url(#wb-sticky-shadow)"/><circle r="14.5" fill="#fff"/>{face}</g>'
        f"{badge}"
        f'<foreignObject class="wb-thread" x="20" y="-18" width="{fmt(_THREAD_W + 2 * _CARD_ROOM)}" height="40" '
        'display="none"><div xmlns="http://www.w3.org/1999/xhtml" class="wb-thread-room">'
        f'<div class="wb-thread-card">{_thread_html(c)}</div></div>'
        "</foreignObject></g></g>"
    )


def thread_text(c: Comment) -> str:
    """A thread as plain text, a post a paragraph: who, when, what."""
    lines = ["Resolved"] if c.resolved else []
    for post in c.posts:
        head = ", ".join(x for x in (post.author or "Someone", post.when) if x)
        lines.append(f"{head}: {post.markdown.strip()}")
    return "\n\n".join(lines) or "Comment"


def _thread_html(c: Comment) -> str:
    parts = ['<div class="wb-thread-state">✓ Resolved</div>'] if c.resolved else []
    for post in c.posts:
        stamp = post.when
        parts.append(
            f'<div class="wb-post"><div class="wb-post-head"><b>{_xml_escape(post.author or "Someone")}</b>'
            + (f" · {_xml_escape(stamp)}" if stamp else "")
            + f'</div><div class="wb-post-body">{post.html}</div></div>'
        )
    return "".join(parts) or '<div class="wb-post">No text was read for this comment.</div>'


def _initials(name: str | None) -> str:
    words = [w for w in (name or "").split() if w[:1].isalnum()]
    return "".join(w[0] for w in (words[:1] + words[1:][-1:])).upper() or "?"


def _author_colour(author_id: str | None) -> str:
    digest = hashlib.sha1((author_id or "").encode()).digest()
    return _PIN_COLOURS[digest[0] % len(_PIN_COLOURS)]


def _defs(caps: set[tuple[str, int]]) -> str:
    return "<defs>" + SHADOWS + "".join(_marker(name, size) for name, size in sorted(caps)) + "</defs>"


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
# As in the editor, the space where a line wraps stays on the line: it must
# fit there, and it counts when the line is aligned (break-spaces).
_STYLE = (
    "<style>"
    f".node-text{{display:flex;flex-direction:column;margin:0;padding:{fmt(TEXT_PADDING)}px;"
    f"color:{DEFAULT_STROKE};line-height:{LINE_HEIGHT:.4f};height:100%;width:100%;"
    "box-sizing:border-box;overflow:hidden;white-space:break-spaces;}"
    ".node-free{display:block;height:auto;width:auto;padding:0;overflow:visible;}"
    ".node-body{margin:0;overflow-wrap:anywhere;}"
    ".va-top{margin-bottom:auto;}"
    ".va-middle{margin-top:auto;margin-bottom:auto;}"
    ".va-bottom{margin-top:auto;}"
    # No space between paragraphs or round lists: the editor sizes a box as
    # whole 22px lines plus padding.
    ".node-body p{margin:0;}"
    f".node-body ul,.node-body ol{{margin:0 0 0 {LIST_INDENT_EM}em;padding:0;text-align:left;}}"
    ".node-body li{margin:0;}"
    ".node-body li>p{margin:0;}"
    f".node-body strong{{font-weight:{EDITOR_BOLD_WEIGHT};}}"
    + "".join(f".node-body h{n}{{font-size:{size / BASE_FONT_PX:.4f}em;line-height:{line / size:.4f};"
              f"margin:{EDITOR_HEADING_ABOVE[n] / size:.4f}em 0 {EDITOR_HEADING_BELOW / size:.4f}em;"
              f"font-weight:{EDITOR_HEADING_WEIGHTS[n]};}}"
              for n, (size, line) in HEADINGS.items())
    # The space round headings (EDITOR_HEADING_ABOVE): none at either end, nor before a list.
    + ".node-body>:first-child{margin-top:0;}.node-body>:last-child{margin-bottom:0;}"
    + ",".join(f".node-body h{n}:has(+ul),.node-body h{n}:has(+ol)" for n in HEADINGS) + "{margin-bottom:0;}"
    + f".wb-section-title{{font-size:{fmt(BASE_FONT_PX)}px;font-weight:{EDITOR_BOLD_WEIGHT};white-space:nowrap;"
    "box-sizing:border-box;width:100%;height:100%;}"
    ".wb-section-clip{overflow:hidden;text-overflow:ellipsis;}"
    + ".wb-label-box{display:flex;align-items:center;justify-content:center;width:100%;height:100%;}"
    f".wb-label-box>.node-body{{background:#FFFFFF;padding:0 4px;line-height:{LINE_HEIGHT:.4f};white-space:nowrap;text-align:center;}}"
    "</style>"
)


def _render_node(node: Node, box: Box, smap: dict[int, int], board: Board) -> str:
    if node.kind is Kind.SHAPE:
        inner = _shape(node, box, smap, board.drawings)
    elif node.kind is Kind.TEXT:
        inner = _free_text(node, box)
    elif node.kind is Kind.IMAGE and node.image:
        x, y, w, h = box
        if node.image.href:
            href = _xml_escape(node.image.href)
            inner = f'<image x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" href="{href}"/>'
        else:  # not downloaded: a placeholder rather than a broken link
            inner = (
                f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" fill="#F1F2F4" '
                f'stroke="{drawn("#B3B9C4")}" stroke-width="1" stroke-dasharray="4,3"/>'
                f'<text x="{fmt(x + w / 2)}" y="{fmt(y + h / 2)}" text-anchor="middle" dominant-baseline="middle" '
                f'font-size="{fmt(min(12.0, h / 3))}" fill="#626F86">image</text>'
            )
    elif node.kind is Kind.ICON:
        inner = _icon(node, box, board.icons)
    elif node.kind is Kind.STICKY:
        inner = _sticky(node, box)
    elif node.kind is Kind.SECTION:
        inner = _section_frame(node, box)
    elif node.kind is Kind.STICKER:
        inner = _sticker_image(node, box, board.stickers)
    elif node.kind is Kind.STAMP:
        inner = _sticker_image(node, box, board.stamps)
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
    if node.rotation:
        kind += f' data-r="{_fraction(node.rotation)}"'
    if node.parent:  # the viewer moves it with the element it was put on
        kind += f' data-parent="{_xml_escape(node.parent)}"'
    return (
        f'<g class="{cls}" data-id="{_xml_escape(node.id)}"{kind} '
        f'data-x="{fmt(x)}" data-y="{fmt(y)}" data-w="{fmt(w)}" data-h="{fmt(h)}">'
        f"{inner}</g>"
    )


def _shape(node: Node, box: Box, smap: dict[int, int], drawings: dict[int, dict]) -> str:
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return ""
    shape = drawing(node.shape_kind, x, y, w, h, smap, drawings)
    # The editor draws the outline inside the box: its outer edge is the box
    # edge (but see inset_outline). The text area stays the whole box's.
    i = OUTLINE_INSET if shape.inset else 0.0
    outline = drawing(node.shape_kind, x + i, y + i, max(w - 2 * i, 0.0), max(h - 2 * i, 0.0), smap, drawings)
    colours = {
        "fill": node.fill.hex if node.fill else "transparent",
        "stroke": _hex(node.stroke, DEFAULT_STROKE),
    }
    markup = "".join(_section(i, sec, colours, node.stroke_style) for i, sec in enumerate(outline.sections))
    if not node.html or shape.text is None or shape.text[2] <= 0 or shape.text[3] <= 0:
        return markup
    tx, ty, tw, th = shape.text
    return markup + (
        f'<foreignObject x="{fmt(tx)}" y="{fmt(ty)}" width="{fmt(tw)}" height="{fmt(th)}">'
        f'<div xmlns="http://www.w3.org/1999/xhtml" class="node-text" '
        f'style="text-align:{node.align};font-size:{_font_px(node)}px{_padding(node)}">'
        f'<div class="node-body va-{node.valign}">{node.html}</div></div>'
        "</foreignObject>"
    )


def _section(index: int, sec: Section, colours: dict[str, str], style: str) -> str:
    """A fill section as one path; a stroke section as one path per subpath,
    each with its own dash layout."""
    colour = colours.get(sec.colour, sec.colour)  # a role, or a colour of its own
    if sec.paint == "fill":
        rule = ' fill-rule="evenodd"' if sec.rule == "evenodd" else ""
        alpha = f' fill-opacity="{fmt(sec.opacity)}"' if sec.opacity < 1 else ""
        return f'<path data-part="{index}" d="{sec.d}" fill="{colour}"{rule}{alpha} stroke="none"/>'
    if colour == "transparent" or style == "none":
        return ""
    width = SHAPE_LINE_WIDTH
    paths = []
    for j, sub in enumerate(sec.subpaths):
        dashes, offset = dash_layout(sub, width, sec.dash_mode) if style == "dashed" else ([], 0.0)
        if dashes:
            attrs = (
                f'stroke="{colour}" stroke-width="{fmt(width)}" stroke-dasharray="{",".join(_fine(v) for v in dashes)}" '
                f'stroke-dashoffset="{_fine(offset)}" stroke-linecap="round"'
            )
        else:
            attrs = _stroke("solid" if style == "dashed" else style, colour, width)
        paths.append(f'<path data-part="{index}" data-sub="{j}" d="{sub.d}" fill="none" {attrs}/>')
    return "".join(paths)


def _fraction(v: float) -> str:
    """An anchor's fraction of its box. Unlike a coordinate it needs more
    than one decimal: 0.0388 of a 550-high box is 21 units off the corner."""
    return f"{round(v, 5):g}"


def _sticky(node: Node, box: Box) -> str:
    """A coloured square with a soft shadow; its text padded as in a shape."""
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return ""
    out = (f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" '
           f'fill="{_hex(node.fill, "#FCE4A6")}" filter="url(#wb-sticky-shadow)"/>')
    if node.html:
        out += (
            f'<foreignObject x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}">'
            f'<div xmlns="http://www.w3.org/1999/xhtml" class="node-text" '
            f'style="text-align:{node.align};font-size:{_font_px(node)}px{_padding(node)}">'
            f'<div class="node-body va-{node.valign}">{node.html}</div></div>'
            "</foreignObject>"
        )
    return out


def _sticker_image(node: Node, box: Box, stickers: dict[str, str]) -> str:
    """A sticker or stamp: its picture (the image read for the board),
    turned as on the board; without it, a tile naming it."""
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return ""
    turn = (f' transform="rotate({fmt(math.degrees(node.rotation))} {fmt(x + w / 2)} {fmt(y + h / 2)})"'
            if node.rotation else "")
    if node.sprite and (href := stickers.get(node.sprite)):
        return (f'<image x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" href="{_xml_escape(href)}" '
                f'preserveAspectRatio="xMidYMid meet"{turn}/>')
    return (
        f'<g{turn}><rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" rx="{fmt(min(w, h) * 0.15)}" '
        f'fill="#F7F8F9" stroke="#B7B9BE" stroke-width="1.5" stroke-dasharray="4,3"/>'
        f'<foreignObject x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}">'
        f'<div xmlns="http://www.w3.org/1999/xhtml" class="node-text" style="font-size:10px;color:#626F86;'
        f'text-align:center"><div class="node-body va-middle">{_xml_escape(node.sprite or "sticker")}</div></div>'
        "</foreignObject></g>"
    )


def section_tab(node: Node, box: Box) -> Box | None:
    """Where a section's title tab is drawn, if it has a title."""
    if not node.title:
        return None
    x, y, w, _ = box
    width = min(w / 2 - SECTION_TAB_ROOM, SVG_METRICS.width(node.title, bold=True) + SECTION_TAB_EXTRA)
    return x, y - SECTION_TAB_RISE - SECTION_TAB_H / 2, width, SECTION_TAB_H


def _section_frame(node: Node, box: Box) -> str:
    """The frame: fill and border, and the title on its tab above."""
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return ""
    border = _hex(node.stroke, "#DDDEE1")
    fill = _hex(node.fill, "#FFFFFF")
    if node.shadow:  # a shadow instead of a border
        out = (f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" rx="{fmt(SECTION_RADIUS)}" '
               f'fill="{fill}" filter="url(#wb-section-shadow)"/>')
    else:
        i = SECTION_BORDER / 2  # drawn inside the box, as shape outlines are
        out = (f'<rect x="{fmt(x + i)}" y="{fmt(y + i)}" width="{fmt(max(w - 2 * i, 0))}" '
               f'height="{fmt(max(h - 2 * i, 0))}" rx="{fmt(SECTION_RADIUS)}" fill="{fill}" stroke="{border}" '
               f'stroke-width="{fmt(SECTION_BORDER)}"/>')
    if tab := section_tab(node, box):
        tx, ty, tw, th = tab
        # Cut short (with an ellipsis) only a title wider than its room.
        clip = " wb-section-clip" if tw >= w / 2 - SECTION_TAB_ROOM else ""
        out += (
            f'<rect x="{fmt(tx)}" y="{fmt(ty)}" width="{fmt(tw)}" height="{fmt(th)}" rx="{fmt(SECTION_RADIUS)}" '
            f'fill="{border}"/>'
            f'<foreignObject x="{fmt(tx)}" y="{fmt(ty)}" width="{fmt(tw)}" height="{fmt(th)}">'
            f'<div xmlns="http://www.w3.org/1999/xhtml" class="wb-section-title{clip}" '
            f'style="color:{_hex(node.color, "#FFFFFF")};padding:0 0 0 {fmt(SECTION_TAB_PAD)}px;line-height:{fmt(th)}px">'
            f"{_xml_escape(node.title or '')}</div></foreignObject>"
        )
    return out


def _icon(node: Node, box: Box, icons: dict[str, str]) -> str:
    """A library icon: its artwork (the SVG file read for the board), or,
    if that is not available, a rounded square naming it; the element's
    own label below."""
    x, y, w, h = box
    colour = _hex(node.stroke, DEFAULT_STROKE)
    if node.icon_key and (href := icons.get(node.icon_key)):
        out = (f'<image x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" '
               f'href="{_xml_escape(href)}" preserveAspectRatio="xMidYMin meet"/>')
        return out + _icon_label(node, box)
    out = (
        f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" rx="{fmt(min(w, h) * 0.15)}" '
        f'fill="#F7F8F9" stroke="{colour}" stroke-width="1.5" stroke-dasharray="4,3"/>'
        f'<foreignObject x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}">'
        f'<div xmlns="http://www.w3.org/1999/xhtml" class="node-text" style="font-size:10px;color:#626F86;'
        f'text-align:center"><div class="node-body va-middle">{_xml_escape(node.icon or "icon")}</div></div>'
        "</foreignObject>"
    )
    return out + _icon_label(node, box)


def _icon_label(node: Node, box: Box) -> str:
    x, y, w, h = box
    out = ""
    if node.html:
        out += (
            f'<foreignObject x="{fmt(x - w / 2)}" y="{fmt(y + h)}" width="{fmt(2 * w)}" height="{fmt(SVG_METRICS.line_h * 2)}" '
            f'style="overflow:visible"><div xmlns="http://www.w3.org/1999/xhtml" class="node-text node-free" '
            f'style="text-align:center;font-size:{_font_px(node)}px"><div class="node-body">{node.html}</div></div>'
            "</foreignObject>"
        )
    return out


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
        f'font-size:{_font_px(node)}px;padding:{fmt(SVG_METRICS.free_pad / 2)}px;{nowrap}">'
        f'<div class="node-body">{node.html}</div></div>'
        "</foreignObject>"
    )


def _render_edge(edge: Edge, boxes: dict[str, Box], bounds: _Bounds) -> tuple[str, list[str]]:
    """The connector's path, and its labels."""
    # The recorded start/end are computed from a stale element size, so they
    # are only a fallback for an end that is not attached to an element.
    src = anchor_point(boxes[edge.source], edge.source_anchor) if edge.source else edge.start
    tgt = anchor_point(boxes[edge.target], edge.target_anchor) if edge.target else edge.end
    if src is None or tgt is None:
        return "", []
    start = End(src, anchor_side(edge.source_anchor) if edge.source else None, edge.start_cap,
                boxes[edge.source] if edge.source else None)
    end = End(tgt, anchor_side(edge.target_anchor) if edge.target else None, edge.end_cap,
              boxes[edge.target] if edge.target else None)
    path = route(edge.routing, start, end, edge.waypoints, edge.stroke_size, edge.waypoint_axes)
    for p in path.points():
        bounds.point(*p)
    labels = [_render_label(edge, label, path, bounds) for label in edge.labels]

    sa, ta = edge.source_anchor, edge.target_anchor
    markers = "".join(
        f' {attr}="url(#{_marker_id(cap, edge.stroke_size)})"'
        for attr, cap in (("marker-start", edge.start_cap), ("marker-end", edge.end_cap))
        if cap in ARROWHEADS
    )
    (s_ext, s_hide), (t_ext, t_hide) = end_stub(edge.start_cap, edge.stroke_size), end_stub(edge.end_cap, edge.stroke_size)
    stroke = _stroke(edge.stroke_style, _hex(edge.color, EDGE_DEFAULT_STROKE), thickness(edge.stroke_size))
    return (
        f'<path class="wb-edge" data-id="{_xml_escape(edge.id)}" data-src="{_xml_escape(edge.source or "")}" '
        f'data-tgt="{_xml_escape(edge.target or "")}" '
        f'data-sa="{_fraction(sa[0])},{_fraction(sa[1])}" data-ta="{_fraction(ta[0])},{_fraction(ta[1])}" '
        f'data-routing="{edge.routing}" data-wp="{" ".join(f"{fmt(x)},{fmt(y)}" for x, y in edge.waypoints)}" '
        f'data-axes="{" ".join(a or "-" for a in edge.waypoint_axes)}" '
        f'data-ends="{fmt(s_ext)},{int(s_hide)},{fmt(t_ext)},{int(t_hide)}" '
        f'd="{path_data(path)}" fill="none" {stroke}{markers}/>'
    ), labels


def _render_label(edge: Edge, label: Label, path: Route, bounds: _Bounds) -> str:
    """Text on the line, on a background that hides the line behind it, or
    beside the line for a "left"/"right" label."""
    (px, py), angle = point_at(path, label.proportion)
    lines = [ln for ln in label.markdown.splitlines() if ln.strip()] or [" "]
    m = SVG_METRICS.scaled(label.font_scale)
    w = max(m.width(ln) for ln in lines) + 2 * LABEL_PADDING
    h = len(lines) * m.line_h + 2 * LABEL_PADDING
    if label.side != "centre":
        # To the left or right of the way the line runs ("left" of a
        # rightward line is above it: screen coordinates run downward). The
        # box's corner nearest the line is LABEL_GAP from the point, along
        # the normal, and the box reaches away from the line from there.
        sign = 1 if label.side == "left" else -1
        nx, ny = math.sin(angle) * sign, -math.cos(angle) * sign
        px += nx * LABEL_GAP + _sign(nx) * w / 2
        py += ny * LABEL_GAP + _sign(ny) * h / 2
    x, y = px - w / 2, py - h / 2
    bounds.box(x, y, w, h)
    colour = _hex(label.color, DEFAULT_STROKE)
    return (
        f'<g class="wb-label" data-id="{_xml_escape(label.id)}" data-edge="{_xml_escape(edge.id)}" '
        f'data-p="{_fraction(label.proportion)}" data-side="{label.side}">'
        f'<foreignObject x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" style="overflow:visible">'
        f'<div xmlns="http://www.w3.org/1999/xhtml" class="wb-label-box">'
        f'<div class="node-body" style="color:{colour};font-size:{fmt(BASE_FONT_PX * label.font_scale)}px">'
        f"{label.html}</div></div></foreignObject></g>"
    )


def _sign(v: float) -> int:
    return 0 if abs(v) < 1e-9 else 1 if v > 0 else -1


def path_data(path: Route) -> str:
    parts = [f"M {_xy(path.start)}"]
    for seg in path.segments:
        parts.append(f"L {_xy(seg[0])}" if len(seg) == 1 else "C " + ", ".join(_xy(p) for p in seg))
    return " ".join(parts)


def _stroke(style: str, colour: str, width: float) -> str:
    """Stroke attributes for a line style, with the editor's patterns: dashes
    repeat every 12 line widths (60% dash, round caps included), and dots are
    round, one line width across, every 2.4 widths."""
    if style == "none":
        return 'stroke="none"'
    attrs = f'stroke="{colour}" stroke-width="{fmt(width)}"'
    if style == "dashed":
        period = DASH_PERIOD * width
        core, gap = DASH_SHARE * period - width, (1 - DASH_SHARE) * period + width
        return attrs + f' stroke-dasharray="{fmt(core)},{fmt(gap)}" stroke-linecap="round"'
    if style == "dotted":  # zero-length dashes with round caps draw dots
        return attrs + f' stroke-dasharray="0,{fmt(width * 2.4)}" stroke-linecap="round"'
    return attrs


def _fine(v: float) -> str:
    """Two decimals: dash lists are long, and rounding errors add up along them."""
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _xy(p: Point) -> str:
    return f"{fmt(p[0])} {fmt(p[1])}"


def _hex(c: Rgb | None, default: str) -> str:
    return c.hex if c else default


def _font_px(node: Node) -> str:
    return fmt(BASE_FONT_PX * node.font_scale)


def _padding(node: Node) -> str:
    return "" if node.font_scale == 1 else f";padding:{fmt(TEXT_PADDING * node.font_scale)}px"
