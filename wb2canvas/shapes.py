"""Shape stereotypes: SVG outline geometry and safe text areas.

Confluence stores a numeric shape kind; DEFAULT_SHAPE_MAP names the kinds
confirmed against real boards, and callers may extend it.
"""

from __future__ import annotations

import math
from collections.abc import Callable


def fmt(v: float | int) -> str:
    """Compact number formatting for SVG attributes."""
    if isinstance(v, int):
        return str(v)
    return f"{v:.1f}".rstrip("0").rstrip(".")


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
        f"M {fmt(x)} {fmt(y + e)} "
        f"A {fmt(rx)} {fmt(e)} 0 0 1 {fmt(x + w)} {fmt(y + e)} "
        f"L {fmt(x + w)} {fmt(y + h - e)} "
        f"A {fmt(rx)} {fmt(e)} 0 0 1 {fmt(x)} {fmt(y + h - e)} "
        f"Z "
        f"M {fmt(x)} {fmt(y + e)} "
        f"A {fmt(rx)} {fmt(e)} 0 0 0 {fmt(x + w)} {fmt(y + e)}"
    )


def _ellipse_path(x: float, y: float, w: float, h: float) -> str:
    cx, cy = x + w / 2, y + h / 2
    rx, ry = w / 2, h / 2
    return (
        f"M {fmt(cx - rx)} {fmt(cy)} "
        f"A {fmt(rx)} {fmt(ry)} 0 0 1 {fmt(cx + rx)} {fmt(cy)} "
        f"A {fmt(rx)} {fmt(ry)} 0 0 1 {fmt(cx - rx)} {fmt(cy)} Z"
    )


def _diamond_path(x: float, y: float, w: float, h: float) -> str:
    cx, cy = x + w / 2, y + h / 2
    return (
        f"M {fmt(cx)} {fmt(y)} "
        f"L {fmt(x + w)} {fmt(cy)} "
        f"L {fmt(cx)} {fmt(y + h)} "
        f"L {fmt(x)} {fmt(cy)} Z"
    )


def _triangle_path(x: float, y: float, w: float, h: float) -> str:
    return (
        f"M {fmt(x + w / 2)} {fmt(y)} "
        f"L {fmt(x + w)} {fmt(y + h)} "
        f"L {fmt(x)} {fmt(y + h)} Z"
    )


def _hexagon_path(x: float, y: float, w: float, h: float) -> str:
    inset = min(w * 0.20, 32.0)
    cy = y + h / 2
    return (
        f"M {fmt(x + inset)} {fmt(y)} "
        f"L {fmt(x + w - inset)} {fmt(y)} "
        f"L {fmt(x + w)} {fmt(cy)} "
        f"L {fmt(x + w - inset)} {fmt(y + h)} "
        f"L {fmt(x + inset)} {fmt(y + h)} "
        f"L {fmt(x)} {fmt(cy)} Z"
    )


def _pentagon_path(x: float, y: float, w: float, h: float) -> str:
    cx = x + w / 2
    notch = h * 0.30
    return (
        f"M {fmt(cx)} {fmt(y)} "
        f"L {fmt(x + w)} {fmt(y + notch)} "
        f"L {fmt(x + w * 0.85)} {fmt(y + h)} "
        f"L {fmt(x + w * 0.15)} {fmt(y + h)} "
        f"L {fmt(x)} {fmt(y + notch)} Z"
    )


def _octagon_path(x: float, y: float, w: float, h: float) -> str:
    inset_x = min(w * 0.30, 40.0)
    inset_y = min(h * 0.30, 40.0)
    return (
        f"M {fmt(x + inset_x)} {fmt(y)} "
        f"L {fmt(x + w - inset_x)} {fmt(y)} "
        f"L {fmt(x + w)} {fmt(y + inset_y)} "
        f"L {fmt(x + w)} {fmt(y + h - inset_y)} "
        f"L {fmt(x + w - inset_x)} {fmt(y + h)} "
        f"L {fmt(x + inset_x)} {fmt(y + h)} "
        f"L {fmt(x)} {fmt(y + h - inset_y)} "
        f"L {fmt(x)} {fmt(y + inset_y)} Z"
    )


def _parallelogram_path(x: float, y: float, w: float, h: float) -> str:
    skew = min(w * 0.18, 32.0)
    return (
        f"M {fmt(x + skew)} {fmt(y)} "
        f"L {fmt(x + w)} {fmt(y)} "
        f"L {fmt(x + w - skew)} {fmt(y + h)} "
        f"L {fmt(x)} {fmt(y + h)} Z"
    )


def _trapezoid_path(x: float, y: float, w: float, h: float) -> str:
    inset = min(w * 0.18, 32.0)
    return (
        f"M {fmt(x + inset)} {fmt(y)} "
        f"L {fmt(x + w - inset)} {fmt(y)} "
        f"L {fmt(x + w)} {fmt(y + h)} "
        f"L {fmt(x)} {fmt(y + h)} Z"
    )


def _note_path(x: float, y: float, w: float, h: float) -> str:
    """Sticky note with folded corner top-right."""
    fold = min(w * 0.15, h * 0.20, 22.0)
    return (
        f"M {fmt(x)} {fmt(y)} "
        f"L {fmt(x + w - fold)} {fmt(y)} "
        f"L {fmt(x + w)} {fmt(y + fold)} "
        f"L {fmt(x + w)} {fmt(y + h)} "
        f"L {fmt(x)} {fmt(y + h)} Z "
        f"M {fmt(x + w - fold)} {fmt(y)} "
        f"L {fmt(x + w - fold)} {fmt(y + fold)} "
        f"L {fmt(x + w)} {fmt(y + fold)}"
    )


def _document_path(x: float, y: float, w: float, h: float) -> str:
    """Document with wavy bottom edge."""
    wave = min(h * 0.12, 22.0)
    return (
        f"M {fmt(x)} {fmt(y)} "
        f"L {fmt(x + w)} {fmt(y)} "
        f"L {fmt(x + w)} {fmt(y + h - wave)} "
        f"Q {fmt(x + w * 0.75)} {fmt(y + h)}, "
        f"{fmt(x + w * 0.5)} {fmt(y + h - wave / 2)} "
        f"Q {fmt(x + w * 0.25)} {fmt(y + h - wave)}, "
        f"{fmt(x)} {fmt(y + h - wave / 2)} Z"
    )


def _cloud_path(x: float, y: float, w: float, h: float) -> str:
    """Five-bump cloud approximation."""
    return (
        f"M {fmt(x + w * 0.18)} {fmt(y + h * 0.65)} "
        f"C {fmt(x - w * 0.05)} {fmt(y + h * 0.65)}, "
        f"{fmt(x - w * 0.05)} {fmt(y + h * 0.30)}, "
        f"{fmt(x + w * 0.20)} {fmt(y + h * 0.30)} "
        f"C {fmt(x + w * 0.18)} {fmt(y - h * 0.05)}, "
        f"{fmt(x + w * 0.50)} {fmt(y - h * 0.05)}, "
        f"{fmt(x + w * 0.55)} {fmt(y + h * 0.18)} "
        f"C {fmt(x + w * 0.65)} {fmt(y - h * 0.05)}, "
        f"{fmt(x + w * 0.95)} {fmt(y + h * 0.05)}, "
        f"{fmt(x + w * 0.85)} {fmt(y + h * 0.30)} "
        f"C {fmt(x + w * 1.05)} {fmt(y + h * 0.35)}, "
        f"{fmt(x + w * 1.05)} {fmt(y + h * 0.70)}, "
        f"{fmt(x + w * 0.85)} {fmt(y + h * 0.70)} "
        f"C {fmt(x + w * 0.85)} {fmt(y + h * 1.05)}, "
        f"{fmt(x + w * 0.40)} {fmt(y + h * 1.05)}, "
        f"{fmt(x + w * 0.30)} {fmt(y + h * 0.78)} "
        f"C {fmt(x + w * 0.05)} {fmt(y + h * 0.85)}, "
        f"{fmt(x - w * 0.05)} {fmt(y + h * 0.65)}, "
        f"{fmt(x + w * 0.18)} {fmt(y + h * 0.65)} Z"
    )


def _star_path(x: float, y: float, w: float, h: float) -> str:
    """Five-pointed star inscribed in the bbox."""
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
        pts.append(f"{fmt(px)} {fmt(py)}")
    return "M " + " L ".join(pts) + " Z"


# Registry of stereotype-name → path-generator. Add new ones here.
GENERATORS: dict[str, Callable[[float, float, float, float], str]] = {
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


STEREOTYPES: frozenset[str] = frozenset({"rect", *GENERATORS})


# Per-stereotype text inset (top, right, bottom, left in fractions of w/h)
# so foreignObject text avoids curved or cut-off regions of the shape.
def text_inset(name: str, w: float, h: float) -> tuple[float, float, float, float]:
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


def outline(
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
    gen = GENERATORS.get(name)
    if gen is None:
        return "", "rect"
    return gen(x, y, w, h), name
