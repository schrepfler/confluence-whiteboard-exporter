"""SVG renderer — emits a self-contained SVG that visually approximates the
source whiteboard. Coexists with the JSON Canvas exporter as an alternative
output format optimised for visual fidelity rather than editability.

Why SVG: dashed strokes, rounded corners, exact text positioning, curved
connectors, freehand paths, shape kinds beyond rectangles — all things JSON
Canvas 1.0 cannot represent.
"""

from __future__ import annotations

from typing import Any

from .adf import _xml_escape, adf_to_html, adf_to_markdown
from .convert import _fix_mojibake, rendered_bounds
from .model import ClipboardElement, DumpFile, Vector3


CAP_NONE = 1
CAP_ARROW = 2

STROKE_SOLID = 1
STROKE_DASHED = 2

DEFAULT_STROKE = "#172B4D"
DEFAULT_FILL = "#FFFFFF"
EDGE_DEFAULT_STROKE = "#758195"

MARGIN = 60
RX = 8


# Confluence whiteboard shape kind integers we've observed empirically.
# Only 3 (rect) and 13 (cylinder) are confirmed against actual data; the rest
# are configurable via the shape_map parameter / --shape-map CLI flag.
DEFAULT_SHAPE_MAP: dict[int, str] = {
    3: "rect",
    13: "cylinder",
}


def _cylinder_path(x: float, y: float, w: float, h: float) -> str:
    """Database stereotype: rectangle front + elliptical bottom + visible top rim."""
    e = min(h * 0.14, 22.0)
    rx = w / 2
    return (
        f"M {_fmt(x)} {_fmt(y + e)} "
        f"A {_fmt(rx)} {_fmt(e)} 0 0 1 {_fmt(x + w)} {_fmt(y + e)} "
        f"L {_fmt(x + w)} {_fmt(y + h - e)} "
        f"A {_fmt(rx)} {_fmt(e)} 0 0 1 {_fmt(x)} {_fmt(y + h - e)} "
        f"Z "
        f"M {_fmt(x)} {_fmt(y + e)} "
        f"A {_fmt(rx)} {_fmt(e)} 0 0 0 {_fmt(x + w)} {_fmt(y + e)}"
    )


def _ellipse_path(x: float, y: float, w: float, h: float) -> str:
    cx, cy = x + w / 2, y + h / 2
    rx, ry = w / 2, h / 2
    return (
        f"M {_fmt(cx - rx)} {_fmt(cy)} "
        f"A {_fmt(rx)} {_fmt(ry)} 0 0 1 {_fmt(cx + rx)} {_fmt(cy)} "
        f"A {_fmt(rx)} {_fmt(ry)} 0 0 1 {_fmt(cx - rx)} {_fmt(cy)} Z"
    )


def _diamond_path(x: float, y: float, w: float, h: float) -> str:
    cx, cy = x + w / 2, y + h / 2
    return (
        f"M {_fmt(cx)} {_fmt(y)} "
        f"L {_fmt(x + w)} {_fmt(cy)} "
        f"L {_fmt(cx)} {_fmt(y + h)} "
        f"L {_fmt(x)} {_fmt(cy)} Z"
    )


def _triangle_path(x: float, y: float, w: float, h: float) -> str:
    return (
        f"M {_fmt(x + w / 2)} {_fmt(y)} "
        f"L {_fmt(x + w)} {_fmt(y + h)} "
        f"L {_fmt(x)} {_fmt(y + h)} Z"
    )


def _hexagon_path(x: float, y: float, w: float, h: float) -> str:
    inset = min(w * 0.20, 32.0)
    cy = y + h / 2
    return (
        f"M {_fmt(x + inset)} {_fmt(y)} "
        f"L {_fmt(x + w - inset)} {_fmt(y)} "
        f"L {_fmt(x + w)} {_fmt(cy)} "
        f"L {_fmt(x + w - inset)} {_fmt(y + h)} "
        f"L {_fmt(x + inset)} {_fmt(y + h)} "
        f"L {_fmt(x)} {_fmt(cy)} Z"
    )


def _pentagon_path(x: float, y: float, w: float, h: float) -> str:
    cx = x + w / 2
    notch = h * 0.30
    return (
        f"M {_fmt(cx)} {_fmt(y)} "
        f"L {_fmt(x + w)} {_fmt(y + notch)} "
        f"L {_fmt(x + w * 0.85)} {_fmt(y + h)} "
        f"L {_fmt(x + w * 0.15)} {_fmt(y + h)} "
        f"L {_fmt(x)} {_fmt(y + notch)} Z"
    )


def _octagon_path(x: float, y: float, w: float, h: float) -> str:
    inset_x = min(w * 0.30, 40.0)
    inset_y = min(h * 0.30, 40.0)
    return (
        f"M {_fmt(x + inset_x)} {_fmt(y)} "
        f"L {_fmt(x + w - inset_x)} {_fmt(y)} "
        f"L {_fmt(x + w)} {_fmt(y + inset_y)} "
        f"L {_fmt(x + w)} {_fmt(y + h - inset_y)} "
        f"L {_fmt(x + w - inset_x)} {_fmt(y + h)} "
        f"L {_fmt(x + inset_x)} {_fmt(y + h)} "
        f"L {_fmt(x)} {_fmt(y + h - inset_y)} "
        f"L {_fmt(x)} {_fmt(y + inset_y)} Z"
    )


def _parallelogram_path(x: float, y: float, w: float, h: float) -> str:
    skew = min(w * 0.18, 32.0)
    return (
        f"M {_fmt(x + skew)} {_fmt(y)} "
        f"L {_fmt(x + w)} {_fmt(y)} "
        f"L {_fmt(x + w - skew)} {_fmt(y + h)} "
        f"L {_fmt(x)} {_fmt(y + h)} Z"
    )


def _trapezoid_path(x: float, y: float, w: float, h: float) -> str:
    inset = min(w * 0.18, 32.0)
    return (
        f"M {_fmt(x + inset)} {_fmt(y)} "
        f"L {_fmt(x + w - inset)} {_fmt(y)} "
        f"L {_fmt(x + w)} {_fmt(y + h)} "
        f"L {_fmt(x)} {_fmt(y + h)} Z"
    )


def _note_path(x: float, y: float, w: float, h: float) -> str:
    """Sticky note with folded corner top-right."""
    fold = min(w * 0.15, h * 0.20, 22.0)
    return (
        f"M {_fmt(x)} {_fmt(y)} "
        f"L {_fmt(x + w - fold)} {_fmt(y)} "
        f"L {_fmt(x + w)} {_fmt(y + fold)} "
        f"L {_fmt(x + w)} {_fmt(y + h)} "
        f"L {_fmt(x)} {_fmt(y + h)} Z "
        f"M {_fmt(x + w - fold)} {_fmt(y)} "
        f"L {_fmt(x + w - fold)} {_fmt(y + fold)} "
        f"L {_fmt(x + w)} {_fmt(y + fold)}"
    )


def _document_path(x: float, y: float, w: float, h: float) -> str:
    """Document with wavy bottom edge."""
    wave = min(h * 0.12, 22.0)
    return (
        f"M {_fmt(x)} {_fmt(y)} "
        f"L {_fmt(x + w)} {_fmt(y)} "
        f"L {_fmt(x + w)} {_fmt(y + h - wave)} "
        f"Q {_fmt(x + w * 0.75)} {_fmt(y + h)}, "
        f"{_fmt(x + w * 0.5)} {_fmt(y + h - wave / 2)} "
        f"Q {_fmt(x + w * 0.25)} {_fmt(y + h - wave)}, "
        f"{_fmt(x)} {_fmt(y + h - wave / 2)} Z"
    )


def _cloud_path(x: float, y: float, w: float, h: float) -> str:
    """Five-bump cloud approximation."""
    return (
        f"M {_fmt(x + w * 0.18)} {_fmt(y + h * 0.65)} "
        f"C {_fmt(x - w * 0.05)} {_fmt(y + h * 0.65)}, "
        f"{_fmt(x - w * 0.05)} {_fmt(y + h * 0.30)}, "
        f"{_fmt(x + w * 0.20)} {_fmt(y + h * 0.30)} "
        f"C {_fmt(x + w * 0.18)} {_fmt(y - h * 0.05)}, "
        f"{_fmt(x + w * 0.50)} {_fmt(y - h * 0.05)}, "
        f"{_fmt(x + w * 0.55)} {_fmt(y + h * 0.18)} "
        f"C {_fmt(x + w * 0.65)} {_fmt(y - h * 0.05)}, "
        f"{_fmt(x + w * 0.95)} {_fmt(y + h * 0.05)}, "
        f"{_fmt(x + w * 0.85)} {_fmt(y + h * 0.30)} "
        f"C {_fmt(x + w * 1.05)} {_fmt(y + h * 0.35)}, "
        f"{_fmt(x + w * 1.05)} {_fmt(y + h * 0.70)}, "
        f"{_fmt(x + w * 0.85)} {_fmt(y + h * 0.70)} "
        f"C {_fmt(x + w * 0.85)} {_fmt(y + h * 1.05)}, "
        f"{_fmt(x + w * 0.40)} {_fmt(y + h * 1.05)}, "
        f"{_fmt(x + w * 0.30)} {_fmt(y + h * 0.78)} "
        f"C {_fmt(x + w * 0.05)} {_fmt(y + h * 0.85)}, "
        f"{_fmt(x - w * 0.05)} {_fmt(y + h * 0.65)}, "
        f"{_fmt(x + w * 0.18)} {_fmt(y + h * 0.65)} Z"
    )


def _star_path(x: float, y: float, w: float, h: float) -> str:
    """Five-pointed star inscribed in the bbox."""
    import math

    cx, cy = x + w / 2, y + h / 2
    rx, ry = w / 2, h / 2
    inner_x, inner_y = rx * 0.40, ry * 0.40
    pts: list[str] = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        r_x = rx if i % 2 == 0 else inner_x
        r_y = ry if i % 2 == 0 else inner_y
        px = cx + math.cos(angle) * r_x
        py = cy + math.sin(angle) * r_y
        pts.append(f"{_fmt(px)} {_fmt(py)}")
    return "M " + " L ".join(pts) + " Z"


# Registry of stereotype-name → path-generator. Add new ones here.
_PATH_GENERATORS: dict[str, callable] = {  # type: ignore[type-arg]
    "cylinder": _cylinder_path,
    "ellipse": _ellipse_path,
    "diamond": _diamond_path,
    "triangle": _triangle_path,
    "hexagon": _hexagon_path,
    "pentagon": _pentagon_path,
    "octagon": _octagon_path,
    "parallelogram": _parallelogram_path,
    "trapezoid": _trapezoid_path,
    "note": _note_path,
    "document": _document_path,
    "cloud": _cloud_path,
    "star": _star_path,
}


# Per-stereotype text inset (top, right, bottom, left in fractions of w/h)
# so foreignObject text avoids curved or cut-off regions of the shape.
def _text_inset_px(name: str, w: float, h: float) -> tuple[float, float, float, float]:
    if name == "cylinder":
        e = min(h * 0.14, 22.0)
        return (e, 0.0, e, 0.0)
    if name == "ellipse":
        return (h * 0.15, w * 0.15, h * 0.15, w * 0.15)
    if name == "diamond":
        return (h * 0.25, w * 0.25, h * 0.25, w * 0.25)
    if name == "triangle":
        return (h * 0.45, w * 0.20, 0.0, w * 0.20)
    if name == "hexagon":
        inset = min(w * 0.20, 32.0)
        return (0.0, inset, 0.0, inset)
    if name == "pentagon":
        return (h * 0.10, w * 0.15, h * 0.05, w * 0.15)
    if name == "octagon":
        inset_x = min(w * 0.20, 28.0)
        inset_y = min(h * 0.20, 28.0)
        return (inset_y, inset_x, inset_y, inset_x)
    if name == "parallelogram":
        skew = min(w * 0.18, 32.0)
        return (0.0, skew, 0.0, skew)
    if name == "trapezoid":
        inset = min(w * 0.18, 32.0)
        return (0.0, inset, 0.0, inset)
    if name == "note":
        fold = min(w * 0.15, h * 0.20, 22.0)
        return (0.0, fold, 0.0, 0.0)
    if name == "document":
        wave = min(h * 0.12, 22.0)
        return (0.0, 0.0, wave, 0.0)
    if name == "cloud":
        return (h * 0.18, w * 0.10, h * 0.18, w * 0.10)
    if name == "star":
        return (h * 0.30, w * 0.30, h * 0.30, w * 0.30)
    return (0.0, 0.0, 0.0, 0.0)


def _shape_outline(
    kind: int | None,
    x: float,
    y: float,
    w: float,
    h: float,
    shape_map: dict[int, str],
) -> tuple[str, str]:
    """Returns (svg_d, stereotype_name). svg_d is empty for plain rect (caller
    should emit <rect> instead of <path>)."""
    name = shape_map.get(kind if kind is not None else -1, "rect")
    gen = _PATH_GENERATORS.get(name)
    if gen is None:
        return "", "rect"
    return gen(x, y, w, h), name


def dump_to_svg(
    dump: DumpFile,
    vault_prefix: str = "",
    shape_map: dict[int, str] | None = None,
) -> str:
    smap = dict(DEFAULT_SHAPE_MAP)
    if shape_map:
        smap.update(shape_map)
    bounds = _compute_bounds(dump)
    if bounds is None:
        return _empty_svg()
    min_x, min_y, max_x, max_y = bounds
    vb_x = int(min_x - MARGIN)
    vb_y = int(min_y - MARGIN)
    vb_w = int((max_x - min_x) + 2 * MARGIN)
    vb_h = int((max_y - min_y) + 2 * MARGIN)

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="{vb_x} {vb_y} {vb_w} {vb_h}" '
        f'width="{vb_w}" height="{vb_h}" '
        f'font-family="-apple-system, BlinkMacSystemFont, sans-serif" '
        f'font-size="13">'
    )
    parts.append(_defs())
    parts.append(_style_block())

    # z-order: paths first (background dividers), then images, then shapes/text,
    # then connectors on top so arrows are visible.
    for idx, elem in enumerate(dump.elements):
        if elem.type == "path":
            s = _render_path(elem, idx)
            if s:
                parts.append(s)
    for idx, elem in enumerate(dump.elements):
        if elem.type == "image":
            s = _render_image(elem, idx, dump, vault_prefix)
            if s:
                parts.append(s)
    for idx, elem in enumerate(dump.elements):
        if elem.type in ("shape", "text"):
            s = _render_shape(elem, idx, smap)
            if s:
                parts.append(s)
    for idx, elem in enumerate(dump.elements):
        if elem.type == "connector":
            s = _render_connector(elem, idx, dump)
            if s:
                parts.append(s)

    parts.append(_interactive_script())
    parts.append("</svg>")
    return "\n".join(parts)


def _defs() -> str:
    return (
        '<defs>'
        '<marker id="arrow" viewBox="0 0 12 12" refX="11" refY="6" '
        'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
        '<path d="M 0 0 L 12 6 L 0 12 z" fill="context-stroke" />'
        '</marker>'
        '</defs>'
    )


def _style_block() -> str:
    # Embedded CSS scoped to this SVG; tightens up foreignObject HTML rendering.
    # height:100% + overflow:hidden + box-sizing:border-box make the inner div
    # respect the foreignObject bounds so text doesn't spill outside the rect.
    return (
        "<style>"
        ".node-text{"
        "margin:0;padding:8px 12px;color:#172B4D;"
        "font-size:13px;line-height:1.35;"
        "height:100%;width:100%;box-sizing:border-box;overflow:hidden;"
        "}"
        ".node-text p{margin:0 0 2px 0;}"
        ".node-text ul,.node-text ol{margin:1px 0 1px 14px;padding:0;}"
        ".node-text li{margin:0;}"
        ".node-text li>p{margin:0;}"
        ".node-text strong{font-weight:600;}"
        "</style>"
    )


def _render_shape(elem: ClipboardElement, idx: int, shape_map: dict[int, str]) -> str:
    if not elem.position:
        return ""
    md = _fix_mojibake(adf_to_markdown(elem.text)) if elem.text else None
    x, y, w, h = rendered_bounds(elem, markdown=md)
    if w <= 0 or h <= 0:
        return ""

    fill = _hex(elem.color) or DEFAULT_FILL if elem.fillEnabled else "transparent"
    stroke = _hex(elem.strokeColor) or _hex(elem.color) or DEFAULT_STROKE
    stroke_w = max(1.0, float(elem.stroke or 1)) * 1.5
    dash = _stroke_dash(elem.strokeStyle, stroke_w)

    kind = elem.shape if elem.type == "shape" else None
    d, name = _shape_outline(kind, x, y, w, h, shape_map)

    if name == "rect":
        outline = (
            f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" height="{_fmt(h)}" '
            f'rx="{RX}" ry="{RX}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{_fmt(stroke_w)}" {dash}/>'
        )
    else:
        outline = (
            f'<path d="{d}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{_fmt(stroke_w)}" {dash}/>'
        )

    top, right, bottom, left = _text_inset_px(name, w, h)
    text_x = x + left
    text_y = y + top
    text_w = max(0.0, w - left - right)
    text_h = max(0.0, h - top - bottom)

    text_html = adf_to_html(_fix_mojibake(elem.text) if elem.text else None) if elem.type == "shape" or elem.text else ""
    if text_html and text_w > 0 and text_h > 0:
        body = (
            f'<foreignObject x="{_fmt(text_x)}" y="{_fmt(text_y)}" '
            f'width="{_fmt(text_w)}" height="{_fmt(text_h)}">'
            f'<div xmlns="http://www.w3.org/1999/xhtml" class="node-text">{text_html}</div>'
            f'</foreignObject>'
        )
    else:
        body = ""

    return (
        f'<g class="wb-node" data-idx="{idx}" '
        f'data-x="{_fmt(x)}" data-y="{_fmt(y)}" '
        f'data-w="{_fmt(w)}" data-h="{_fmt(h)}">'
        f"{outline}{body}</g>"
    )


def _render_connector(elem: ClipboardElement, idx: int, dump: DumpFile) -> str:
    endpoints = _connector_endpoints(elem, dump)
    if endpoints is None:
        return ""
    sx, sy, ex, ey = endpoints
    color = _hex(elem.color) or EDGE_DEFAULT_STROKE
    width = max(1.0, float(elem.stroke or 1)) * 1.5
    dash = _stroke_dash(elem.strokeStyle, width)
    marker_end = ' marker-end="url(#arrow)"' if elem.endCap == CAP_ARROW else ""
    marker_start = ' marker-start="url(#arrow)"' if elem.startCap == CAP_ARROW else ""

    s_dir = _anchor_to_direction(elem.sourceAnchor)
    e_dir = _anchor_to_direction(elem.targetAnchor)
    d = _build_curve(sx, sy, ex, ey, s_dir, e_dir)

    sa_left = float(getattr(elem.sourceAnchor, "left", 0.5)) if elem.sourceAnchor else 0.5
    sa_top = float(getattr(elem.sourceAnchor, "top", 0.5)) if elem.sourceAnchor else 0.5
    ta_left = float(getattr(elem.targetAnchor, "left", 0.5)) if elem.targetAnchor else 0.5
    ta_top = float(getattr(elem.targetAnchor, "top", 0.5)) if elem.targetAnchor else 0.5
    src_idx = elem.sourceIndex if elem.sourceIndex is not None else -1
    tgt_idx = elem.targetIndex if elem.targetIndex is not None else -1

    return (
        f'<path class="wb-edge" data-idx="{idx}" '
        f'data-src="{src_idx}" data-tgt="{tgt_idx}" '
        f'data-sa="{sa_left},{sa_top}" data-ta="{ta_left},{ta_top}" '
        f'data-startcap="{1 if elem.startCap == CAP_ARROW else 0}" '
        f'data-endcap="{1 if elem.endCap == CAP_ARROW else 0}" '
        f'd="{d}" stroke="{color}" stroke-width="{_fmt(width)}" '
        f'fill="none" {dash}{marker_start}{marker_end}/>'
    )


def _connector_endpoints(
    elem: ClipboardElement, dump: DumpFile
) -> tuple[float, float, float, float] | None:
    """Compute connector endpoints from source/target shape geometry + anchors.
    Falls back to the recorded start/end coordinates when refs are missing.
    The recorded start/end are sometimes internal control points rather than
    edge anchors, so this is more reliable for visual fidelity."""
    src_pt = _anchor_point(elem.sourceIndex, elem.sourceAnchor, dump)
    tgt_pt = _anchor_point(elem.targetIndex, elem.targetAnchor, dump)

    if src_pt is None and elem.start and len(elem.start) >= 2:
        src_pt = (float(elem.start[0]), float(elem.start[1]))
    if tgt_pt is None and elem.end and len(elem.end) >= 2:
        tgt_pt = (float(elem.end[0]), float(elem.end[1]))

    if src_pt is None or tgt_pt is None:
        return None
    return src_pt[0], src_pt[1], tgt_pt[0], tgt_pt[1]


def _anchor_point(
    idx: int | None, anchor: Any, dump: DumpFile
) -> tuple[float, float] | None:
    if idx is None or idx < 0 or idx >= len(dump.elements):
        return None
    shape = dump.elements[idx]
    if not shape.position:
        return None
    md = _fix_mojibake(adf_to_markdown(shape.text)) if shape.text else None
    bx, by, bw, bh = rendered_bounds(shape, markdown=md)
    if bw <= 0 or bh <= 0:
        return None
    left = float(getattr(anchor, "left", 0.5)) if anchor else 0.5
    top = float(getattr(anchor, "top", 0.5)) if anchor else 0.5
    return bx + bw * left, by + bh * top


def _anchor_to_direction(anchor: Any) -> str | None:
    """Return which edge the connector exits/enters: left|right|top|bottom."""
    if anchor is None:
        return None
    left = float(getattr(anchor, "left", 0.5))
    top = float(getattr(anchor, "top", 0.5))
    # Cardinal anchors are typically exactly 0 or 1 on one axis with 0.5 on the other.
    if left <= 0.05:
        return "left"
    if left >= 0.95:
        return "right"
    if top <= 0.05:
        return "top"
    if top >= 0.95:
        return "bottom"
    return None


def _build_curve(
    sx: float,
    sy: float,
    ex: float,
    ey: float,
    s_dir: str | None,
    e_dir: str | None,
) -> str:
    dx = ex - sx
    dy = ey - sy
    span = max(abs(dx), abs(dy))
    handle = max(40.0, span * 0.4)

    c1x, c1y = _control_point(sx, sy, s_dir, handle, dx, dy, leaving=True)
    c2x, c2y = _control_point(ex, ey, e_dir, handle, dx, dy, leaving=False)

    return (
        f"M {_fmt(sx)} {_fmt(sy)} C {_fmt(c1x)} {_fmt(c1y)}, "
        f"{_fmt(c2x)} {_fmt(c2y)}, {_fmt(ex)} {_fmt(ey)}"
    )


def _control_point(
    x: float,
    y: float,
    direction: str | None,
    handle: float,
    dx: float,
    dy: float,
    leaving: bool,
) -> tuple[float, float]:
    """Push the control point AWAY from the box (along the exit direction) when
    leaving, or AWAY from the target box (so curve enters from the right side)
    when arriving. The marker-end then points along the entry tangent."""
    if direction == "right":
        return x + handle, y
    if direction == "left":
        return x - handle, y
    if direction == "top":
        return x, y - handle
    if direction == "bottom":
        return x, y + handle
    # No anchor info: bias horizontally based on overall direction
    if abs(dx) >= abs(dy):
        return (x + handle if leaving else x - handle), y
    return x, (y + handle if leaving else y - handle)


def _render_path(elem: ClipboardElement, idx: int) -> str:
    if not elem.start or not elem.end or len(elem.start) < 2 or len(elem.end) < 2:
        return ""
    sx, sy = float(elem.start[0]), float(elem.start[1])
    ex, ey = float(elem.end[0]), float(elem.end[1])
    color = _hex(elem.color) or EDGE_DEFAULT_STROKE
    width = max(1.0, float(elem.stroke or 1)) * 1.5
    dash = _stroke_dash(elem.strokeStyle, width)
    return (
        f'<line x1="{_fmt(sx)}" y1="{_fmt(sy)}" x2="{_fmt(ex)}" y2="{_fmt(ey)}" '
        f'stroke="{color}" stroke-width="{_fmt(width)}" {dash}/>'
    )


def _render_image(elem: ClipboardElement, idx: int, dump: DumpFile, vault_prefix: str) -> str:
    if not elem.fileId or not elem.position or not elem.size:
        return ""
    href = dump.media.get(elem.fileId) or f"media/{elem.fileId}"
    if vault_prefix:
        href = vault_prefix.rstrip("/") + "/" + href.lstrip("/")
    href = _xml_escape(href)
    return (
        f'<g class="wb-node" data-idx="{idx}" '
        f'data-x="{_fmt(elem.position.x)}" data-y="{_fmt(elem.position.y)}" '
        f'data-w="{_fmt(elem.size.x)}" data-h="{_fmt(elem.size.y)}">'
        f'<image x="{_fmt(elem.position.x)}" y="{_fmt(elem.position.y)}" '
        f'width="{_fmt(elem.size.x)}" height="{_fmt(elem.size.y)}" href="{href}"/>'
        f'</g>'
    )


def _stroke_dash(style: int | None, stroke_w: float) -> str:
    if style == STROKE_DASHED:
        a = max(4.0, stroke_w * 3)
        b = max(3.0, stroke_w * 2)
        return f'stroke-dasharray="{_fmt(a)},{_fmt(b)}" '
    return ""


def _hex(v: Vector3 | None) -> str | None:
    if v is None:
        return None
    r, g, b = (max(0, min(255, int(round(c)))) for c in (v.x, v.y, v.z))
    return f"#{r:02X}{g:02X}{b:02X}"


def _fmt(v: float | int) -> str:
    if isinstance(v, int):
        return str(v)
    return f"{v:.1f}".rstrip("0").rstrip(".")


_INTERACTIVE_JS = r"""
(function () {
  if (typeof document === 'undefined') return;
  function init() {
    var svg = document.documentElement;
    if (!svg || svg.tagName.toLowerCase() !== 'svg') {
      svg = document.querySelector('svg');
      if (!svg) return;
    }
    var nodeMap = new Map();
    var edgeList = [];
    var nodeEls = svg.querySelectorAll('g.wb-node');
    for (var i = 0; i < nodeEls.length; i++) {
      var g = nodeEls[i];
      var idx = parseInt(g.getAttribute('data-idx'), 10);
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
        srcIdx: parseInt(p.getAttribute('data-src'), 10),
        tgtIdx: parseInt(p.getAttribute('data-tgt'), 10),
        sa: sa, ta: ta
      });
    }
    autoFit();
    redrawAllEdges();
    enablePanZoom(svg);

    function autoFit() {
      // Only grow simple <rect> shapes — paths (cylinders, etc.) and images
      // would need shape-specific path recomputation, skip those for now.
      nodeMap.forEach(function (nd) {
        var fo = nd.foreignObject;
        var rect = nd.rect;
        if (!fo || !rect) return;
        var div = fo.querySelector('div');
        if (!div) return;
        var natural = div.getBoundingClientRect();
        if (!natural || !natural.height) return;
        var ctm = svg.getScreenCTM();
        var scaleY = ctm ? ctm.d : 1;
        var contentH = natural.height / scaleY;
        var foH = parseFloat(fo.getAttribute('height')) || 0;
        if (contentH > foH + 1) {
          fo.setAttribute('height', contentH);
          rect.setAttribute('height', contentH);
          var rectH = parseFloat(rect.getAttribute('height')) || 0;
          nd.group.setAttribute('data-h', rectH);
        }
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


def _empty_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        'width="100" height="100"></svg>'
    )


def _compute_bounds(dump: DumpFile) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for elem in dump.elements:
        if elem.position:
            xs.append(elem.position.x)
            ys.append(elem.position.y)
            if elem.size:
                xs.append(elem.position.x + elem.size.x)
                ys.append(elem.position.y + elem.size.y)
        if elem.basisPosition and elem.basisSize:
            xs.append(elem.basisPosition.x)
            ys.append(elem.basisPosition.y)
            xs.append(elem.basisPosition.x + elem.basisSize.x)
            ys.append(elem.basisPosition.y + elem.basisSize.y)
        for pt in (elem.start, elem.end):
            if pt and len(pt) >= 2:
                xs.append(float(pt[0]))
                ys.append(float(pt[1]))
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)
