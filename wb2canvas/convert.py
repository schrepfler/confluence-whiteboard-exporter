from __future__ import annotations

import json
import math
import re
import struct
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adf import adf_to_markdown
from .storage import atomic_write_text
from .model import (
    Anchor,
    CanvasDoc,
    CanvasEdge,
    CanvasNode,
    ClipboardElement,
    DumpFile,
    FiberDump,
    Vector3,
)


CAP_NONE = 1
CAP_ARROW = 2


# Obsidian's JSON Canvas preset color palette ("1"-"6"). Snapping to a preset
# lets the theme restyle the node (e.g. in dark mode); exact hex is kept when
# no preset is close. These RGB anchors are coarse stand-ins for the themed
# colours, used only to decide which preset is nearest.
_PRESETS = {
    "1": (0xFF, 0x00, 0x00),  # red
    "2": (0xFF, 0xA5, 0x00),  # orange
    "3": (0xFF, 0xFF, 0x00),  # yellow
    "4": (0x00, 0x80, 0x00),  # green
    "5": (0x00, 0xFF, 0xFF),  # cyan
    "6": (0x80, 0x00, 0x80),  # purple
}
_PRESET_DIST_THRESHOLD = 110  # accept a preset if RGB Euclidean distance < this


def dump_to_canvas(
    dump: DumpFile,
    vault_prefix: str = "",
    resolve_collisions: bool = False,
) -> CanvasDoc:
    if dump.strategy == "fiber" and dump.fiber_dump is not None:
        return fiber_dump_to_canvas(dump.fiber_dump, dump.media, vault_prefix=vault_prefix)

    nodes_by_index: dict[int, CanvasNode] = {}

    for idx, elem in enumerate(dump.elements):
        if elem.type in {"shape", "text"}:
            nodes_by_index[idx] = _shape_or_text_to_node(elem, idx)
        elif elem.type == "image":
            node = _image_to_node(elem, idx, dump, vault_prefix=vault_prefix)
            if node is not None:
                nodes_by_index[idx] = node
        elif elem.type == "path":
            node = _path_to_node(elem, idx)
            if node is not None:
                nodes_by_index[idx] = node
        # connector handled in second pass; pathWaypoint dropped (Obsidian has no curve primitive)

    edges: list[CanvasEdge] = []
    for idx, elem in enumerate(dump.elements):
        if elem.type != "connector":
            continue
        edge = _connector_to_edge(elem, idx, nodes_by_index)
        if edge is not None:
            edges.append(edge)

    nodes = [nodes_by_index[i] for i in sorted(nodes_by_index.keys())]
    if resolve_collisions:
        push_apart(nodes)
    return CanvasDoc(nodes=nodes, edges=edges)


# Public so tests and other consumers can call it directly.
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

    # Mark intentional stacks before any movement, using ORIGINAL positions.
    is_stack = [False] * n
    orig = [(nd.x, nd.y) for nd in nodes]
    for i in range(n):
        for j in range(i + 1, n):
            if (
                abs(orig[i][0] - orig[j][0]) <= stack_threshold
                and abs(orig[i][1] - orig[j][1]) <= stack_threshold
            ):
                is_stack[i] = True
                is_stack[j] = True

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
        if (a.x + a.width / 2) <= (b.x + b.width / 2):
            a.x -= push
            b.x += push
        else:
            a.x += push
            b.x -= push
    else:
        push = (overlap_y + padding + 1) // 2
        if (a.y + a.height / 2) <= (b.y + b.height / 2):
            a.y -= push
            b.y += push
        else:
            a.y += push
            b.y -= push
    return True


def fiber_dump_to_canvas(
    fd: FiberDump,
    media: dict[str, str],
    vault_prefix: str = "",
) -> CanvasDoc:
    dims: dict[str, dict[str, Any]] = defaultdict(dict)
    for entry in fd.dimensions:
        key = entry.get("key", "")
        prefix, _, eid = key.partition("#")
        if eid:
            dims[eid][prefix] = entry.get("val")

    z_order = list(fd.zindex) if fd.zindex else list(fd.board.keys())
    seen = set(z_order)
    for k in fd.board:
        if k not in seen:
            z_order.append(k)

    nodes: list[CanvasNode] = []
    edges: list[CanvasEdge] = []

    for elem_id in z_order:
        elem = fd.board.get(elem_id)
        if not elem:
            continue
        t = elem.get("t")
        d = dims.get(elem_id, {})
        pos = d.get("p") or [0, 0]
        size = d.get("s") or [100, 100]
        x = int(round(_fnum(pos[0])))
        y = int(round(_fnum(pos[1])))
        w = int(round(_fnum(size[0])))
        h = int(round(_fnum(size[1])))

        if t == "connector":
            se, te = elem.get("se"), elem.get("te")
            if not se or not te:
                continue
            edges.append(
                CanvasEdge(
                    id=elem_id,
                    fromNode=str(se),
                    toNode=str(te),
                    fromSide=_anchor_dict_to_side(elem.get("sa")),
                    toSide=_anchor_dict_to_side(elem.get("ta")),
                    fromEnd="arrow" if elem.get("sc") == CAP_ARROW else "none",
                    toEnd="arrow" if elem.get("ec") == CAP_ARROW else "none",
                    color=_fiber_color(elem.get("c")),
                )
            )
        elif t in ("shape", "text"):
            nodes.append(
                CanvasNode(
                    id=elem_id,
                    type="text",
                    x=x,
                    y=y,
                    width=w,
                    height=h,
                    color=_fiber_color(elem.get("c")),
                    text=None,
                )
            )
        elif t == "image":
            fi = elem.get("fi")
            if not fi:
                continue
            file_path = media.get(fi) or f"media/{fi}"
            nodes.append(
                CanvasNode(
                    id=elem_id,
                    type="file",
                    x=x,
                    y=y,
                    width=w,
                    height=h,
                    file=_apply_prefix(file_path, vault_prefix),
                )
            )
        # path / pathWaypoint dropped

    return CanvasDoc(nodes=nodes, edges=edges)


def _apply_prefix(file_path: str, prefix: str) -> str:
    if not prefix:
        return file_path
    return prefix.rstrip("/") + "/" + file_path.lstrip("/")


def _fnum(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _fiber_color(c: Any) -> str | None:
    if c is None:
        return None
    bytes_list: list[int] = []
    if isinstance(c, dict):
        for i in range(12):
            v = c.get(str(i), c.get(i))
            if v is None:
                return None
            bytes_list.append(int(v))
    elif isinstance(c, list) and len(c) >= 12:
        bytes_list = [int(b) for b in c[:12]]
    else:
        return None
    try:
        floats = struct.unpack(">3f", bytes(bytes_list))
    except (struct.error, ValueError):
        return None
    r, g, b = (max(0, min(255, int(round(v)))) for v in floats)
    return f"#{r:02X}{g:02X}{b:02X}"


def _anchor_dict_to_side(a: Any) -> str | None:
    if not isinstance(a, dict):
        return None
    left = _fnum(a.get("left", 0.5))
    top = _fnum(a.get("top", 0.5))
    cx, cy = left - 0.5, top - 0.5
    if abs(cx) >= abs(cy):
        return "right" if cx > 0 else "left"
    return "bottom" if cy > 0 else "top"


def convert_file(
    dump_path: Path,
    canvas_path: Path | None = None,
    vault_prefix: str = "",
    resolve_collisions: bool = False,
) -> tuple[Path, CanvasDoc]:
    dump = DumpFile.model_validate_json(dump_path.read_text())
    doc = dump_to_canvas(dump, vault_prefix=vault_prefix, resolve_collisions=resolve_collisions)
    canvas_path = canvas_path or dump_path.with_name(f"{dump.board.boardId}.canvas")
    atomic_write_text(canvas_path, json.dumps(doc.model_dump(exclude_none=True), indent=2))
    return canvas_path, doc


def _id(elem: ClipboardElement, idx: int) -> str:
    return f"n{idx}"


def _color_hex(v: Vector3 | None, snap_preset: bool = False) -> str | None:
    if v is None:
        return None
    r, g, b = (max(0, min(255, int(round(c)))) for c in (v.x, v.y, v.z))
    if snap_preset:
        preset = _nearest_preset(r, g, b)
        if preset is not None:
            return preset
    return f"#{r:02X}{g:02X}{b:02X}"


def _nearest_preset(r: int, g: int, b: int) -> str | None:
    best: tuple[float, str] | None = None
    for key, (pr, pg, pb) in _PRESETS.items():
        d = ((r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2) ** 0.5
        if best is None or d < best[0]:
            best = (d, key)
    if best is None or best[0] > _PRESET_DIST_THRESHOLD:
        return None
    return best[1]


@dataclass(frozen=True)
class TextMetrics:
    """Rough text layout model for one renderer; used only to size boxes."""

    char_w: float  # average glyph advance, px
    line_h: float  # line height, px
    pad_w: float  # total horizontal padding inside a node, px
    pad_h: float  # total vertical padding inside a node, px
    para_gap: float  # extra height per blank line, in lines


# Obsidian canvas cards render ~16px text with generous card padding.
CANVAS_METRICS = TextMetrics(char_w=7.5, line_h=22.0, pad_w=32.0, pad_h=36.0, para_gap=0.5)
# The SVG renders 13px system text at line-height 1.35 with 8/12px padding.
SVG_METRICS = TextMetrics(char_w=6.6, line_h=17.6, pad_w=24.0, pad_h=16.0, para_gap=0.15)

_LIST_PREFIX = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"(\*\*|\*|`|~~)")


def _plain(line: str) -> tuple[str, bool]:
    """Visible text of a markdown line, and whether it is a list item."""
    is_item = bool(_LIST_PREFIX.match(line))
    text = _LIST_PREFIX.sub("", line)
    text = _MD_LINK.sub(r"\1", text)
    return _MD_MARKS.sub("", text).strip(), is_item


def _unwrapped_bounds(markdown: str, m: TextMetrics) -> tuple[float, float]:
    """Size of text that never wraps (auto-width free text)."""
    width, lines = 0.0, 0.0
    for raw in markdown.splitlines():
        text, is_item = _plain(raw)
        if not text:
            lines += m.para_gap
            continue
        width = max(width, (len(text) + (3 if is_item else 0)) * m.char_w)
        lines += 1
    return width + m.pad_w, lines * m.line_h + m.pad_h


def _wrapped_height(markdown: str, width: float, m: TextMetrics) -> float:
    """Height of text word-wrapped to `width`."""
    usable = max(m.char_w * 4, width - m.pad_w)
    lines = 0.0
    for raw in markdown.splitlines():
        text, is_item = _plain(raw)
        if not text:
            lines += m.para_gap
            continue
        avail = usable - (3 * m.char_w if is_item else 0)
        per_line = max(1, int(avail // m.char_w))
        lines += max(1, math.ceil(len(text) / per_line))
    return lines * m.line_h + m.pad_h


def rendered_bounds(
    elem: ClipboardElement,
    markdown: str | None = None,
    metrics: TextMetrics = CANVAS_METRICS,
) -> tuple[float, float, float, float]:
    """The box the source application actually draws for an element.

    Shapes are drawn at basisPosition/basisSize. Their `size` is not usable:
    on a sample board every shape stores the same 160x160 default, while
    basisSize widths match the drawn widths to within a few px. Text is
    wrapped at that width, and only the height may grow, because the target
    renderer's font can be larger than Confluence's.

    Free text (type "text") is the reverse: its basisSize is a placeholder and
    the text auto-widens, so the box comes from the content.
    """
    if not elem.position:
        return 0.0, 0.0, 0.0, 0.0
    if elem.type == "text":
        x, y = elem.position.x, elem.position.y
        w = elem.size.x if elem.size else 0.0
        h = elem.size.y if elem.size else 0.0
        if markdown:
            tw, th = _unwrapped_bounds(markdown, metrics)
            w, h = max(w, tw), max(h, th)
        return x, y, w, h

    if elem.basisSize and elem.basisSize.x > 0 and elem.basisSize.y > 0:
        origin = elem.basisPosition or elem.position
        x, y, w, h = origin.x, origin.y, elem.basisSize.x, elem.basisSize.y
    else:
        x, y = elem.position.x, elem.position.y
        w = elem.size.x if elem.size else 0.0
        h = elem.size.y if elem.size else 0.0
    if markdown and w > 0:
        h = max(h, _wrapped_height(markdown, w, metrics))
    return x, y, w, h


def _xywh(elem: ClipboardElement, markdown: str | None = None) -> tuple[int, int, int, int]:
    px, py, sw, sh = rendered_bounds(elem, markdown=markdown)
    return int(round(px)), int(round(py)), int(round(sw)), int(round(sh))


def _shape_or_text_to_node(elem: ClipboardElement, idx: int) -> CanvasNode:
    md = _fix_mojibake(adf_to_markdown(elem.text)) or None
    x, y, w, h = _xywh(elem, markdown=md)
    return CanvasNode(
        id=_id(elem, idx),
        type="text",
        x=x,
        y=y,
        width=w,
        height=h,
        color=_pick_node_color(elem),
        text=md,
    )


def _fix_mojibake(s: str | None) -> str | None:
    """Recover characters that were UTF-8 encoded but read as Latin-1.
    Common pattern in older clipboard dumps where atob's binary string was
    JSON-parsed without a TextDecoder pass (typographic apostrophe shows up
    as 'â€™'). Idempotent: returns the original string if no recovery is
    possible."""
    if not s or "Â" not in s and "â" not in s:
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _pick_node_color(elem: ClipboardElement) -> str | None:
    """Outline-only shapes (fillEnabled=false) carry their visual identity in
    strokeColor; filled shapes carry it in color. Snap to Obsidian preset when close."""
    primary = elem.strokeColor if elem.fillEnabled is False else elem.color
    fallback = elem.color if elem.fillEnabled is False else elem.strokeColor
    return _color_hex(primary, snap_preset=True) or _color_hex(fallback, snap_preset=True)


def _image_to_node(
    elem: ClipboardElement,
    idx: int,
    dump: DumpFile,
    vault_prefix: str = "",
) -> CanvasNode | None:
    file_id = elem.fileId
    if not file_id:
        return None
    file_path = dump.media.get(file_id) or f"media/{file_id}"
    x, y, w, h = _xywh(elem, markdown=None)
    return CanvasNode(
        id=_id(elem, idx),
        type="file",
        x=x,
        y=y,
        width=w,
        height=h,
        file=_apply_prefix(file_path, vault_prefix),
    )


def _path_to_node(elem: ClipboardElement, idx: int) -> CanvasNode | None:
    """Render a freehand line as a thin colored rectangle approximating its
    bounding box. Loses curvature but preserves dividers and underlines."""
    if not elem.start or not elem.end or len(elem.start) < 2 or len(elem.end) < 2:
        return None
    sx, sy = float(elem.start[0]), float(elem.start[1])
    ex, ey = float(elem.end[0]), float(elem.end[1])
    x0, x1 = min(sx, ex), max(sx, ex)
    y0, y1 = min(sy, ey), max(sy, ey)
    thickness = max(2, int(elem.stroke or 2) * 2)
    width = max(int(round(x1 - x0)), thickness)
    height = max(int(round(y1 - y0)), thickness)
    return CanvasNode(
        id=_id(elem, idx),
        type="text",
        x=int(round(x0)),
        y=int(round(y0)),
        width=width,
        height=height,
        color=_color_hex(elem.color, snap_preset=True),
        text=None,
    )


def _connector_to_edge(
    elem: ClipboardElement,
    idx: int,
    nodes_by_index: dict[int, CanvasNode],
) -> CanvasEdge | None:
    src_idx = elem.sourceIndex
    tgt_idx = elem.targetIndex
    if src_idx is None or tgt_idx is None:
        return None
    src = nodes_by_index.get(src_idx)
    tgt = nodes_by_index.get(tgt_idx)
    if src is None or tgt is None:
        return None
    return CanvasEdge(
        id=f"e{idx}",
        fromNode=src.id,
        toNode=tgt.id,
        fromSide=_anchor_to_side(elem.sourceAnchor),
        toSide=_anchor_to_side(elem.targetAnchor),
        fromEnd="arrow" if elem.startCap == CAP_ARROW else "none",
        toEnd="arrow" if elem.endCap == CAP_ARROW else "none",
        color=_color_hex(elem.color),
    )


def _anchor_to_side(a: Anchor | None) -> str | None:
    if a is None:
        return None
    cx, cy = a.left - 0.5, a.top - 0.5
    if abs(cx) >= abs(cy):
        return "right" if cx > 0 else "left"
    return "bottom" if cy > 0 else "top"
