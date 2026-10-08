"""JSON Canvas 1.0 renderer (https://jsoncanvas.org), aimed at Obsidian."""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .board import CANVAS_METRICS, Board, Kind, Node, Rgb, anchor_side, layout

log = logging.getLogger(__name__)

Side = Literal["top", "right", "bottom", "left"]
End = Literal["none", "arrow"]

# JSON Canvas has one line-end marker; every arrowhead maps to it.
ARROW_CAPS = frozenset({"arrow", "filled-arrow", "open-arrow"})


class CanvasNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["text", "file", "link", "group"]
    x: int
    y: int
    width: int
    height: int
    color: str | None = None
    text: str | None = None
    file: str | None = None
    subpath: str | None = None
    url: str | None = None
    label: str | None = None
    background: str | None = None
    backgroundStyle: Literal["cover", "ratio", "repeat"] | None = None


class CanvasEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    fromNode: str
    toNode: str
    fromSide: Side | None = None
    toSide: Side | None = None
    fromEnd: Literal["none", "arrow"] | None = None
    toEnd: Literal["none", "arrow"] | None = None
    color: str | None = None
    label: str | None = None


class CanvasDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[CanvasNode] = Field(default_factory=list)
    edges: list[CanvasEdge] = Field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(exclude_none=True), indent=2)


# Obsidian's preset palette ("1"-"6"). Snapping to a preset lets the theme
# restyle the node (e.g. in dark mode); exact hex is kept when no preset is
# close. These RGB anchors are coarse stand-ins for the themed colours, used
# only to decide which preset is nearest.
_PRESETS = {
    "1": (0xFF, 0x00, 0x00),  # red
    "2": (0xFF, 0xA5, 0x00),  # orange
    "3": (0xFF, 0xFF, 0x00),  # yellow
    "4": (0x00, 0x80, 0x00),  # green
    "5": (0x00, 0xFF, 0xFF),  # cyan
    "6": (0x80, 0x00, 0x80),  # purple
}
_PRESET_MAX_DISTANCE = 110
_MIN_LINE_THICKNESS = 2


def render_canvas(
    board: Board,
    vault_prefix: str = "",
    resolve_collisions: bool = False,
) -> CanvasDoc:
    """`vault_prefix` is prepended to image paths, which Obsidian resolves
    from the vault root rather than from the canvas file."""
    boxes = layout(board, CANVAS_METRICS)
    # Sections are groups, listed first so they sit under what lies on them.
    ordered = sorted(board.nodes, key=lambda n: n.kind is not Kind.SECTION)
    nodes = [_node(n, boxes[n.id], vault_prefix, board) for n in ordered]
    dropped_caps: Counter[str] = Counter()
    edges: list[CanvasEdge] = []
    loose = 0
    for e in board.edges:
        if not (e.source and e.target):  # JSON Canvas edges must join two nodes
            loose += 1
            continue
        edges.append(CanvasEdge(
            id=e.id,
            fromNode=e.source,
            toNode=e.target,
            fromSide=anchor_side(e.source_anchor),
            toSide=anchor_side(e.target_anchor),
            fromEnd=_end(e.start_cap, dropped_caps),
            toEnd=_end(e.end_cap, dropped_caps),
            color=e.color.hex if e.color else None,
            label=" / ".join(lb.markdown for lb in e.labels if lb.markdown) or None,
        ))
    _warn(board, loose, dropped_caps)
    if resolve_collisions:
        push_apart(nodes)
    return CanvasDoc(nodes=nodes, edges=edges)


def _end(cap: str, dropped: Counter[str]) -> End:
    if cap in ARROW_CAPS:
        return "arrow"
    if cap != "none":
        dropped[cap] += 1
    return "none"


def _warn(board: Board, loose: int, dropped_caps: Counter[str]) -> None:
    where = f"board {board.meta.boardId} canvas"
    if loose:
        log.warning("%s: omitted %d connector(s) with an end not attached to an element", where, loose)
    if dropped_caps:
        caps = ", ".join(f"{n} {c}" for c, n in sorted(dropped_caps.items()))
        log.warning("%s: JSON Canvas has no such line ends; drawn plain: %s", where, caps)


def _node(n: Node, box: tuple[float, float, float, float], vault_prefix: str, board: Board) -> CanvasNode:
    x, y, w, h = (int(round(v)) for v in box)
    if n.kind is Kind.IMAGE and n.image:
        if n.image.href is None:  # not downloaded
            return CanvasNode(id=n.id, type="text", x=x, y=y, width=w, height=h, text="*(image not downloaded)*")
        return CanvasNode(id=n.id, type="file", x=x, y=y, width=w, height=h,
                          file=_join(vault_prefix, n.image.href))
    if n.kind is Kind.ICON and n.icon_key and (href := board.icons.get(n.icon_key)):  # its SVG file
        return CanvasNode(id=n.id, type="file", x=x, y=y, width=w, height=h, file=_join(vault_prefix, href))
    images = board.stamps if n.kind is Kind.STAMP else board.stickers
    if n.kind in (Kind.STICKER, Kind.STAMP) and n.sprite and (href := images.get(n.sprite)):  # its image
        return CanvasNode(id=n.id, type="file", x=x, y=y, width=w, height=h, file=_join(vault_prefix, href))
    if n.kind in (Kind.STICKER, Kind.STAMP):  # its image was not read: name it
        return CanvasNode(id=n.id, type="text", x=x, y=y, width=w, height=h, text=f"*{n.sprite or 'sticker'}*")
    if n.kind is Kind.ICON:  # its artwork is not available: name it
        text = f"*{n.icon}*" + (f"\n\n{n.markdown}" if n.markdown else "")
        return CanvasNode(id=n.id, type="text", x=x, y=y, width=w, height=h, color=_color(n.stroke), text=text)
    if n.kind is Kind.SECTION:
        return CanvasNode(id=n.id, type="group", x=x, y=y, width=w, height=h, color=_color(n.fill), label=n.title)
    if n.kind is Kind.LINE:
        # No line primitive: a thin coloured card stands in for dividers.
        t = max(_MIN_LINE_THICKNESS, int(n.stroke_width * 2))
        return CanvasNode(id=n.id, type="text", x=x, y=y, width=max(w, t), height=max(h, t),
                          color=_color(n.stroke))
    return CanvasNode(id=n.id, type="text", x=x, y=y, width=w, height=h,
                      color=_color(_identity_colour(n)), text=n.markdown)


def _identity_colour(n: Node) -> Rgb | None:
    """The colour a reader identifies the node by: free text by its text,
    an unfilled shape by its outline, a filled shape by its fill."""
    if n.kind is Kind.TEXT:
        return n.color
    return n.fill or (n.stroke if n.stroke_style != "none" else None)


def _color(c: Rgb | None) -> str | None:
    if c is None:
        return None
    best = min(
        _PRESETS.items(),
        key=lambda kv: (c.r - kv[1][0]) ** 2 + (c.g - kv[1][1]) ** 2 + (c.b - kv[1][2]) ** 2,
    )
    pr, pg, pb = best[1]
    if ((c.r - pr) ** 2 + (c.g - pg) ** 2 + (c.b - pb) ** 2) ** 0.5 <= _PRESET_MAX_DISTANCE:
        return best[0]
    return c.hex


def _join(prefix: str, path: str) -> str:
    return f"{prefix.strip('/')}/{path.lstrip('/')}" if prefix.strip("/") else path


def push_apart(
    nodes: list[CanvasNode],
    max_iter: int = 40,
    padding: int = 6,
    stack_threshold: int = 6,
) -> int:
    """Iteratively separate overlapping nodes along the axis of least overlap.
    Mutates `nodes` in place; returns the number of iterations actually run.

    Nodes whose original (x, y) falls within `stack_threshold` of another
    node's are assumed to be a deliberate overlay and are not separated.
    """
    n = len(nodes)
    if n < 2:
        return 0

    orig = [(nd.x, nd.y) for nd in nodes]
    is_stack = [False] * n
    for i in range(n):
        for j in range(i + 1, n):
            if (abs(orig[i][0] - orig[j][0]) <= stack_threshold
                    and abs(orig[i][1] - orig[j][1]) <= stack_threshold):
                is_stack[i] = is_stack[j] = True

    for it in range(1, max_iter + 1):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                if is_stack[i] and is_stack[j]:
                    continue
                if _push_pair(nodes[i], nodes[j], padding):
                    moved = True
        if not moved:
            return it
    return max_iter


def _push_pair(a: CanvasNode, b: CanvasNode, padding: int) -> bool:
    overlap_x = min(a.x + a.width, b.x + b.width) - max(a.x, b.x)
    overlap_y = min(a.y + a.height, b.y + b.height) - max(a.y, b.y)
    if overlap_x <= 0 or overlap_y <= 0:
        return False
    if overlap_x < overlap_y:
        push = (overlap_x + padding + 1) // 2
        sign = -1 if (a.x + a.width / 2) <= (b.x + b.width / 2) else 1
        a.x += sign * push
        b.x -= sign * push
    else:
        push = (overlap_y + padding + 1) // 2
        sign = -1 if (a.y + a.height / 2) <= (b.y + b.height / 2) else 1
        a.y += sign * push
        b.y -= sign * push
    return True
