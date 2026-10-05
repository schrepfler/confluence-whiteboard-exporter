"""Shape kinds, drawn the way the whiteboard editor draws them.

The drawings in shape_data.json come from the editor's own shape
definitions (see scripts/build_shape_data.py). A drawing point is
(x fraction, y fraction, x offset, y offset) and lands at
`corner + fraction * size + offset`: outlines stretch with the box, while
offsets such as rounded corners or a cylinder's lid keep their size.

Icons (server, cloud, user, ...) are Atlassian artwork and are not shipped;
they are drawn as placeholders with their label below, as on the board.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

Box = tuple[float, float, float, float]
Cmd = list[Any]  # ["M", p] | ["L", p] | ["C", p, p, p] | ["Z"], p = [fx, fy, ox, oy]


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
SHARP_RECTANGLE = 0
PLACEHOLDER_KIND = 3  # icons are stood in for by a rounded rectangle


@cache
def _kinds() -> dict[int, dict[str, Any]]:
    data = json.loads(Path(__file__).with_name("shape_data.json").read_text())
    return {int(k): v for k, v in data["kinds"].items()}


def resolve(kind: int | None, shape_map: dict[int, int] | None = None) -> int:
    """The kind whose drawing is used: after the caller's overrides and the
    editor's own reuse of one kind's drawing for another."""
    k = DEFAULT_KIND if kind is None else kind
    k = (shape_map or {}).get(k, k)
    seen = set()
    while (same := _kinds().get(k, {}).get("same_as")) is not None and k not in seen:
        seen.add(k)
        k = same
    return k


def is_icon(kind: int) -> bool:
    return bool(_kinds().get(kind, {}).get("icon"))


def can_draw(kind: int) -> bool:
    """Whether `kind` has a drawing of its own (or reuses one)."""
    k = resolve(kind)
    return k == SHARP_RECTANGLE or "fills" in _kinds().get(k, {})


def kind_label(kind: int | None) -> str:
    k = DEFAULT_KIND if kind is None else kind
    return f"{KIND_NAMES.get(k, 'unknown')} ({k})"


@dataclass(frozen=True)
class Section:
    paint: str  # "fill" or "stroke": how the section is drawn
    colour: str  # "fill" or "stroke": which of the shape's colours it uses
    d: str
    rule: str = "nonzero"


@dataclass(frozen=True)
class Drawing:
    kind: int  # the kind actually drawn
    sections: tuple[Section, ...]  # empty for a sharp rectangle: draw a plain rect
    graphic: Box  # where the drawing sits
    text: Box | None  # where its text goes; None if the kind takes no text
    placeholder: bool = False  # no drawing available: an icon, or an unknown kind

    @property
    def is_rect(self) -> bool:
        return not self.sections


def drawing(kind: int | None, x: float, y: float, w: float, h: float, shape_map: dict[int, int] | None = None) -> Drawing:
    """How to draw a shape of `kind` in the box (x, y, w, h)."""
    k = resolve(kind, shape_map)
    spec = _kinds().get(k)
    box = (x, y, w, h)
    if spec is None or k == SHARP_RECTANGLE:
        return Drawing(k, (), box, box, placeholder=spec is None)

    graphic = box
    if aspect := spec.get("aspect"):  # fixed aspect ratio, aligned to the top
        graphic = (x, y, w, w / aspect)
    text: Box | None = graphic
    if spec.get("no_text"):
        text = None
    elif spec.get("exterior_text") == "+y" and "aspect" in spec:  # label below the graphic
        gh = graphic[3]
        text = (x, y + gh, w, max(h - gh, 24.0))
    elif spec.get("text"):
        (left, top), (right, bottom) = (_point(p, *graphic) for p in spec["text"])
        text = (left, top, right - left, bottom - top)

    if spec.get("icon"):
        return Drawing(k, sections(PLACEHOLDER_KIND, *graphic), graphic, text, placeholder=True)
    return Drawing(k, sections(k, *graphic), graphic, text)


def sections(kind: int, x: float, y: float, w: float, h: float) -> tuple[Section, ...]:
    spec = _kinds()[kind]
    out = []
    for paint in ("fill", "stroke"):
        for sec in spec.get(f"{paint}s", ()):
            other = "stroke" if paint == "fill" else "fill"
            colour = other if sec.get("paint") == other else paint
            out.append(Section(paint, colour, path_data(sec["path"], x, y, w, h), sec.get("rule", "nonzero")))
    return tuple(out)


def stretching_commands(kind: int) -> list[list[Cmd]] | None:
    """The raw commands of each section, in the order `sections` returns
    them, for a drawing that stretches with its box; otherwise None."""
    spec = _kinds().get(kind)
    if not spec or "fills" not in spec or "aspect" in spec:
        return None
    return [sec["path"] for paint in ("fill", "stroke") for sec in spec.get(f"{paint}s", ())]


def path_data(cmds: list[Cmd], x: float, y: float, w: float, h: float) -> str:
    parts = []
    for op, *pts in cmds:
        parts.append(op + "".join(" " + _xy(_point(p, x, y, w, h)) for p in pts))
    return " ".join(parts)


def _point(p: list[float], x: float, y: float, w: float, h: float) -> tuple[float, float]:
    fx, fy, ox, oy = p
    return x + fx * w + ox, y + fy * h + oy


def _xy(p: tuple[float, float]) -> str:
    return f"{fmt(p[0])} {fmt(p[1])}"
