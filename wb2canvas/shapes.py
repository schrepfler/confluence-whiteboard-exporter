"""Shape stereotypes: SVG outline geometry and safe text areas.

Confluence stores a shape's kind as a number; KIND_NAMES gives each its
editor key (see docs/confluence-whiteboard-model.md). DEFAULT_SHAPE_MAP
picks the stereotype drawn for each kind. Kinds it does not cover are drawn
as plain rectangles, which is also what Atlassian's own exporter does.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

Inset = tuple[float, float, float, float]  # top, right, bottom, left


def fmt(v: float | int) -> str:
    """Compact number formatting for SVG attributes."""
    if isinstance(v, int):
        return str(v)
    return f"{v:.1f}".rstrip("0").rstrip(".")


# The editor's shape enum, keyed as in its shape picker and i18n catalog.
KIND_NAMES: dict[int, str] = dict(enumerate([
    "sharp-rectangle", "rectangle", "ellipse", "rounded-rectangle", "diamond",
    "triangle", "upside-down-triangle", "left-parallelogram", "right-parallelogram",
    "start-end", "document", "off-page", "input-output", "database", "sum", "or",
    "predefined-process", "internal-storage", "manual-input", "manual-operation",
    "multiple-documents", "preparation", "hard-disk", "comment-left", "comment-right",
    "stored-data", "delay", "display", "process", "decision", "connector", "merge",
    "cloud", "key", "server", "archive", "browser", "user", "compute", "computer",
    "file", "firewall", "folder", "frontend", "internet", "lock", "mail", "mobile",
    "settings", "shield", "users", "switch", "database-advanced", "alternate-process",
    "use-case", "classifier", "note", "interface-2", "activation", "activity", "actor",
    "assembly", "component", "deletion", "end", "flow-final", "gateway",
    "history-pseudostate", "horizontal-fork", "vertical-fork", "off-page-link",
    "pin-filled-left", "pin-filled-right", "pin-left", "pin-right", "pin",
    "provided-interface", "receive-signal", "required-interface", "send-signal",
    "start", "template", "class", "interface", "node", "container",
    "boundary-object", "entity-object", "control-object",
]))
KIND_BY_NAME: dict[str, int] = {name: kind for kind, name in KIND_NAMES.items()}

# A shape with no kind is the model's default, kind 0.
DEFAULT_KIND = 0

# Kind -> stereotype. Several kinds share a stereotype; a few are
# approximations (multiple-documents as one document, database-advanced as a
# cylinder, start/end/history-pseudostate as plain circles).
DEFAULT_SHAPE_MAP: dict[int, str] = {
    0: "rect",
    1: "rect",
    2: "ellipse",
    3: "rounded-rect",
    4: "diamond",
    5: "triangle",
    6: "down-triangle",
    7: "left-parallelogram",
    8: "right-parallelogram",
    9: "stadium",
    10: "document",
    11: "off-page",
    12: "right-parallelogram",
    13: "cylinder",
    14: "circle-cross",
    15: "circle-plus",
    16: "predefined-process",
    17: "internal-storage",
    18: "manual-input",
    19: "manual-operation",
    20: "document",
    21: "hexagon",
    22: "hard-disk",
    23: "comment-left",
    24: "comment-right",
    25: "stored-data",
    26: "delay",
    27: "display",
    28: "rect",
    29: "diamond",
    30: "ellipse",
    31: "down-triangle",
    32: "cloud",
    52: "cylinder",
    53: "rounded-rect",
    54: "ellipse",
    56: "note",
    59: "rounded-rect",
    64: "ellipse",
    65: "circle-cross",
    66: "diamond",
    67: "ellipse",
    70: "off-page",
    80: "ellipse",
}


@dataclass(frozen=True)
class Outline:
    name: str  # the stereotype drawn
    d: str = ""  # SVG path data; empty for the rectangle family
    radius: float = 0.0  # corner radius, rectangle family only

    @property
    def is_rect(self) -> bool:
        return not self.d


def outline(kind: int | None, x: float, y: float, w: float, h: float, shape_map: dict[int, str]) -> Outline:
    """How to draw a shape of `kind` in the box (x, y, w, h)."""
    name = stereotype(kind, shape_map) or "rect"
    if name in RECT_RADIUS:
        return Outline(name, radius=RECT_RADIUS[name](w, h))
    return Outline(name, d=GENERATORS[name](x, y, w, h))


def stereotype(kind: int | None, shape_map: dict[int, str]) -> str | None:
    """The stereotype `shape_map` names for `kind`, or None if it has none."""
    name = shape_map.get(DEFAULT_KIND if kind is None else kind)
    return name if name in STEREOTYPES else None


def kind_label(kind: int | None) -> str:
    k = DEFAULT_KIND if kind is None else kind
    return f"{KIND_NAMES.get(k, 'unknown')} ({k})"


# ------------------------------------------------------------------ geometry

Point = tuple[float, float]


def _poly(*pts: Point) -> str:
    head, *rest = pts
    return f"M {_xy(head)} " + " ".join(f"L {_xy(p)}" for p in rest) + " Z"


def _xy(p: Point) -> str:
    return f"{fmt(p[0])} {fmt(p[1])}"


def _cylinder_path(x: float, y: float, w: float, h: float) -> str:
    """Database: rectangle front, elliptical bottom, visible top rim."""
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


def _hard_disk_path(x: float, y: float, w: float, h: float) -> str:
    """Direct-access storage: a cylinder lying on its side, face to the right."""
    e = min(w * 0.12, 22.0)
    ry = h / 2
    return (
        f"M {fmt(x + e)} {fmt(y)} "
        f"L {fmt(x + w - e)} {fmt(y)} "
        f"A {fmt(e)} {fmt(ry)} 0 0 1 {fmt(x + w - e)} {fmt(y + h)} "
        f"L {fmt(x + e)} {fmt(y + h)} "
        f"A {fmt(e)} {fmt(ry)} 0 0 1 {fmt(x + e)} {fmt(y)} Z "
        f"M {fmt(x + w - e)} {fmt(y)} "
        f"A {fmt(e)} {fmt(ry)} 0 0 0 {fmt(x + w - e)} {fmt(y + h)}"
    )


def _ellipse_path(x: float, y: float, w: float, h: float) -> str:
    cx, cy = x + w / 2, y + h / 2
    rx, ry = w / 2, h / 2
    return (
        f"M {fmt(cx - rx)} {fmt(cy)} "
        f"A {fmt(rx)} {fmt(ry)} 0 0 1 {fmt(cx + rx)} {fmt(cy)} "
        f"A {fmt(rx)} {fmt(ry)} 0 0 1 {fmt(cx - rx)} {fmt(cy)} Z"
    )


def _circle_cross_path(x: float, y: float, w: float, h: float) -> str:
    """Summing junction: a circle with a diagonal cross."""
    cx, cy, k = x + w / 2, y + h / 2, 0.5 ** 0.5 / 2
    dx, dy = w * k, h * k
    return (
        _ellipse_path(x, y, w, h)
        + f" M {_xy((cx - dx, cy - dy))} L {_xy((cx + dx, cy + dy))}"
        + f" M {_xy((cx + dx, cy - dy))} L {_xy((cx - dx, cy + dy))}"
    )


def _circle_plus_path(x: float, y: float, w: float, h: float) -> str:
    """Or: a circle with an upright cross."""
    cx, cy = x + w / 2, y + h / 2
    return (
        _ellipse_path(x, y, w, h)
        + f" M {_xy((cx, y))} L {_xy((cx, y + h))} M {_xy((x, cy))} L {_xy((x + w, cy))}"
    )


def _diamond_path(x: float, y: float, w: float, h: float) -> str:
    cx, cy = x + w / 2, y + h / 2
    return _poly((cx, y), (x + w, cy), (cx, y + h), (x, cy))


def _triangle_path(x: float, y: float, w: float, h: float) -> str:
    return _poly((x + w / 2, y), (x + w, y + h), (x, y + h))


def _down_triangle_path(x: float, y: float, w: float, h: float) -> str:
    return _poly((x, y), (x + w, y), (x + w / 2, y + h))


def _parallelogram(k: float) -> Callable[[float, float, float, float], str]:
    """Atlassian's geometry: the top edge spans the box and the bottom edge
    is shifted by k * h, so the shape overhangs its box by that much."""

    def path(x: float, y: float, w: float, h: float) -> str:
        s = k * h
        return _poly((x, y), (x + w, y), (x + w + s, y + h), (x + s, y + h))

    return path


PARALLELOGRAM_SKEW = 0.2


def _hexagon_path(x: float, y: float, w: float, h: float) -> str:
    inset = min(w * 0.20, 32.0)
    cy = y + h / 2
    return _poly((x + inset, y), (x + w - inset, y), (x + w, cy),
                 (x + w - inset, y + h), (x + inset, y + h), (x, cy))


def _off_page_path(x: float, y: float, w: float, h: float) -> str:
    """Off-page connector: a box ending in a downward point."""
    return _poly((x, y), (x + w, y), (x + w, y + h * 0.75), (x + w / 2, y + h), (x, y + h * 0.75))


def _predefined_process_path(x: float, y: float, w: float, h: float) -> str:
    """A box with an inner bar on each side."""
    b = min(w * 0.1, 20.0)
    return (
        _poly((x, y), (x + w, y), (x + w, y + h), (x, y + h))
        + f" M {_xy((x + b, y))} L {_xy((x + b, y + h))}"
        + f" M {_xy((x + w - b, y))} L {_xy((x + w - b, y + h))}"
    )


def _internal_storage_path(x: float, y: float, w: float, h: float) -> str:
    """A box with an inner bar along its top and its left side."""
    b = min(w * 0.1, h * 0.2, 20.0)
    return (
        _poly((x, y), (x + w, y), (x + w, y + h), (x, y + h))
        + f" M {_xy((x + b, y))} L {_xy((x + b, y + h))}"
        + f" M {_xy((x, y + b))} L {_xy((x + w, y + b))}"
    )


def _manual_input_path(x: float, y: float, w: float, h: float) -> str:
    """A box whose top edge slopes up to the right."""
    return _poly((x, y + h * 0.25), (x + w, y), (x + w, y + h), (x, y + h))


def _manual_operation_path(x: float, y: float, w: float, h: float) -> str:
    """A trapezoid, wide edge on top."""
    inset = min(w * 0.15, 32.0)
    return _poly((x, y), (x + w, y), (x + w - inset, y + h), (x + inset, y + h))


def _comment(left: bool) -> Callable[[float, float, float, float], str]:
    """Annotation: an open bracket on one side of the text."""

    def path(x: float, y: float, w: float, h: float) -> str:
        arm = min(w * 0.15, 24.0)
        if left:
            return f"M {_xy((x + arm, y))} L {_xy((x, y))} L {_xy((x, y + h))} L {_xy((x + arm, y + h))}"
        return f"M {_xy((x + w - arm, y))} L {_xy((x + w, y))} L {_xy((x + w, y + h))} L {_xy((x + w - arm, y + h))}"

    return path


def _stored_data_path(x: float, y: float, w: float, h: float) -> str:
    """Both sides curve left: convex on the left, concave on the right."""
    r = min(w * 0.12, h * 0.3)
    return (
        f"M {_xy((x + r, y))} L {_xy((x + w, y))} "
        f"Q {_xy((x + w - 2 * r, y + h / 2))} {_xy((x + w, y + h))} "
        f"L {_xy((x + r, y + h))} "
        f"Q {_xy((x - r, y + h / 2))} {_xy((x + r, y))} Z"
    )


def _delay_path(x: float, y: float, w: float, h: float) -> str:
    """Flat left side, semicircular right side."""
    r = min(w / 2, h / 2)
    return (
        f"M {_xy((x, y))} L {_xy((x + w - r, y))} "
        f"A {fmt(r)} {fmt(h / 2)} 0 0 1 {_xy((x + w - r, y + h))} "
        f"L {_xy((x, y + h))} Z"
    )


def _display_path(x: float, y: float, w: float, h: float) -> str:
    """Pointed left side, rounded right side."""
    p = min(w * 0.15, h / 2)
    r = min(w * 0.15, h / 2)
    return (
        f"M {_xy((x, y + h / 2))} L {_xy((x + p, y))} L {_xy((x + w - r, y))} "
        f"A {fmt(r)} {fmt(h / 2)} 0 0 1 {_xy((x + w - r, y + h))} "
        f"L {_xy((x + p, y + h))} Z"
    )


def _note_path(x: float, y: float, w: float, h: float) -> str:
    """Note with a folded corner top-right."""
    fold = min(w * 0.15, h * 0.20, 22.0)
    return (
        _poly((x, y), (x + w - fold, y), (x + w, y + fold), (x + w, y + h), (x, y + h))
        + f" M {_xy((x + w - fold, y))} L {_xy((x + w - fold, y + fold))} L {_xy((x + w, y + fold))}"
    )


def _document_path(x: float, y: float, w: float, h: float) -> str:
    """Document with a wavy bottom edge."""
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


# Stereotypes drawn as <rect>, by corner radius. Atlassian rounds its
# rounded rectangle at a tenth of the width.
RECT_RADIUS: dict[str, Callable[[float, float], float]] = {
    "rect": lambda w, h: 0.0,
    "rounded-rect": lambda w, h: min(0.1 * w, h / 2),
    "stadium": lambda w, h: min(w, h) / 2,
}

# Stereotypes drawn as <path>.
GENERATORS: dict[str, Callable[[float, float, float, float], str]] = {
    "ellipse": _ellipse_path,
    "circle-cross": _circle_cross_path,
    "circle-plus": _circle_plus_path,
    "diamond": _diamond_path,
    "triangle": _triangle_path,
    "down-triangle": _down_triangle_path,
    "left-parallelogram": _parallelogram(PARALLELOGRAM_SKEW),
    "right-parallelogram": _parallelogram(-PARALLELOGRAM_SKEW),
    "hexagon": _hexagon_path,
    "cylinder": _cylinder_path,
    "hard-disk": _hard_disk_path,
    "document": _document_path,
    "off-page": _off_page_path,
    "predefined-process": _predefined_process_path,
    "internal-storage": _internal_storage_path,
    "manual-input": _manual_input_path,
    "manual-operation": _manual_operation_path,
    "comment-left": _comment(left=True),
    "comment-right": _comment(left=False),
    "stored-data": _stored_data_path,
    "delay": _delay_path,
    "display": _display_path,
    "note": _note_path,
    "cloud": _cloud_path,
}

STEREOTYPES: frozenset[str] = frozenset({*RECT_RADIUS, *GENERATORS})


# ----------------------------------------------------------------- text areas


def text_inset(name: str, w: float, h: float) -> Inset:
    """Margins (top, right, bottom, left) that keep text inside the outline."""
    inset = _INSETS.get(name)
    return inset(w, h) if inset else (0.0, 0.0, 0.0, 0.0)


def _even(fx: float, fy: float) -> Callable[[float, float], Inset]:
    return lambda w, h: (h * fy, w * fx, h * fy, w * fx)


_INSETS: dict[str, Callable[[float, float], Inset]] = {
    "stadium": lambda w, h: (0.0, min(w, h) * 0.3, 0.0, min(w, h) * 0.3),
    "ellipse": _even(0.15, 0.15),
    "circle-cross": _even(0.15, 0.15),
    "circle-plus": _even(0.15, 0.15),
    "diamond": _even(0.25, 0.25),
    "triangle": lambda w, h: (h * 0.45, w * 0.20, 0.0, w * 0.20),
    "down-triangle": lambda w, h: (0.0, w * 0.20, h * 0.45, w * 0.20),
    "left-parallelogram": lambda w, h: (0.0, 0.0, 0.0, PARALLELOGRAM_SKEW * h),
    "right-parallelogram": lambda w, h: (0.0, PARALLELOGRAM_SKEW * h, 0.0, 0.0),
    "hexagon": lambda w, h: (0.0, min(w * 0.20, 32.0), 0.0, min(w * 0.20, 32.0)),
    "cylinder": lambda w, h: (min(h * 0.14, 22.0), 0.0, min(h * 0.14, 22.0), 0.0),
    "hard-disk": lambda w, h: (0.0, 2 * min(w * 0.12, 22.0), 0.0, min(w * 0.12, 22.0)),
    "document": lambda w, h: (0.0, 0.0, min(h * 0.12, 22.0), 0.0),
    "off-page": lambda w, h: (0.0, 0.0, h * 0.25, 0.0),
    "predefined-process": lambda w, h: (0.0, min(w * 0.1, 20.0), 0.0, min(w * 0.1, 20.0)),
    "internal-storage": lambda w, h: (min(w * 0.1, h * 0.2, 20.0), 0.0, 0.0, min(w * 0.1, h * 0.2, 20.0)),
    "manual-input": lambda w, h: (h * 0.25, 0.0, 0.0, 0.0),
    "manual-operation": lambda w, h: (0.0, min(w * 0.15, 32.0), 0.0, min(w * 0.15, 32.0)),
    "stored-data": lambda w, h: (0.0, min(w * 0.12, h * 0.3), 0.0, min(w * 0.12, h * 0.3)),
    "delay": lambda w, h: (0.0, min(w / 2, h / 2) * 0.3, 0.0, 0.0),
    "display": lambda w, h: (0.0, min(w * 0.15, h / 2) * 0.3, 0.0, min(w * 0.15, h / 2)),
    "note": lambda w, h: (0.0, min(w * 0.15, h * 0.20, 22.0), 0.0, 0.0),
    "cloud": _even(0.10, 0.18),
}
