"""Renderer-independent model of a whiteboard.

A dump holds one of two raw captures: the canvas's own copy/paste payload
(`clipboard`) or the Yjs document read out of the page (`fiber`).
`from_dump` normalises either into a `Board`, so each renderer is written
once against one model.

Node geometry is the source's own: the boxes Confluence draws. Renderers
call `layout()` to adapt those boxes to their text metrics.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
from collections import defaultdict
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from typing import Any

from .adf import adf_to_html, adf_to_markdown
from .model import Anchor, BoardMeta, ClipboardElement, DumpFile, FiberDump, Vector2, Vector3

CAP_ARROW = 2
STROKE_DASHED = 2
_VALIGN = {0: "top", 1: "middle", 2: "bottom"}  # observed: shapes use 1 (middle)
_HALIGN = frozenset({"left", "center", "right"})

Box = tuple[float, float, float, float]
Point = tuple[float, float]
CENTER: Point = (0.5, 0.5)


# --------------------------------------------------------------------- model


@dataclass(frozen=True)
class Rgb:
    r: int
    g: int
    b: int

    @property
    def hex(self) -> str:
        return f"#{self.r:02X}{self.g:02X}{self.b:02X}"

    @classmethod
    def of(cls, r: float, g: float, b: float) -> Rgb:
        return cls(_channel(r), _channel(g), _channel(b))

    @classmethod
    def from_vector(cls, v: Vector3 | None) -> Rgb | None:
        return None if v is None else cls.of(v.x, v.y, v.z)

    @classmethod
    def from_float32_be(cls, raw: Any) -> Rgb | None:
        """Decode a Yjs colour: 12 bytes holding three big-endian float32s.

        Playwright hands a Uint8Array back as a list or as an index-keyed dict.
        """
        if isinstance(raw, dict):
            data = [raw.get(str(i), raw.get(i)) for i in range(12)]
        elif isinstance(raw, list):
            data = raw[:12]
        else:
            return None
        if len(data) < 12 or any(v is None for v in data):
            return None
        try:
            r, g, b = struct.unpack(">3f", bytes(int(v) for v in data))
        except (struct.error, ValueError):
            return None
        return cls.of(r, g, b)


def _channel(v: float) -> int:
    return max(0, min(255, int(round(v))))


class Kind(StrEnum):
    SHAPE = "shape"
    TEXT = "text"  # free-floating text; sizes to its content
    IMAGE = "image"
    LINE = "line"  # freehand line, e.g. a section divider


@dataclass(frozen=True)
class Image:
    file_id: str
    href: str  # relative to the dump's directory, e.g. "media/<id>.jpg"


@dataclass(eq=False)
class Node:
    id: str
    kind: Kind
    x: float
    y: float
    w: float
    h: float
    fill: Rgb | None = None  # None means unfilled
    stroke: Rgb | None = None
    color: Rgb | None = None  # text colour of free text
    dashed: bool = False
    stroke_width: float = 1.0
    shape_kind: int | None = None
    align: str = "center"
    valign: str = "middle"
    font_scale: float = 1.0
    auto_width: bool = False  # free text that never wraps
    adf: str | None = None  # Atlassian Document Format, as JSON text
    image: Image | None = None
    points: tuple[Point, ...] = ()  # LINE endpoints

    @cached_property
    def markdown(self) -> str | None:
        return (adf_to_markdown(self.adf) or None) if self.adf else None

    @cached_property
    def html(self) -> str:
        return adf_to_html(self.adf) if self.adf else ""


@dataclass(eq=False)
class Edge:
    id: str
    source: str | None  # node id; None when the end is not on the board
    target: str | None
    source_anchor: Point = CENTER  # fraction of the node box (left, top)
    target_anchor: Point = CENTER
    start: Point | None = None  # recorded endpoint, used when source is None
    end: Point | None = None
    start_arrow: bool = False
    end_arrow: bool = False
    color: Rgb | None = None
    dashed: bool = False
    width: float = 1.0


@dataclass(eq=False)
class Board:
    meta: BoardMeta
    nodes: list[Node]  # z-order, back to front
    edges: list[Edge]

    def __post_init__(self) -> None:
        self._by_id = {n.id: n for n in self.nodes}

    def node(self, node_id: str | None) -> Node | None:
        return self._by_id.get(node_id) if node_id else None


def load_board(dump_path: Path) -> Board:
    return from_dump(DumpFile.model_validate_json(dump_path.read_text()))


def from_dump(dump: DumpFile) -> Board:
    if dump.strategy == "fiber" and dump.fiber_dump is not None:
        nodes, edges = _from_fiber(dump.fiber_dump, dump.media)
    else:
        nodes, edges = _from_clipboard(dump.elements, dump.media)
    node_ids = {n.id for n in nodes}
    for e in edges:  # an end that is not a drawn node falls back to its point
        if e.source not in node_ids:
            e.source = None
        if e.target not in node_ids:
            e.target = None
    return Board(meta=dump.board, nodes=nodes, edges=edges)


# ------------------------------------------------------- clipboard adapter


def _from_clipboard(
    elements: list[ClipboardElement], media: dict[str, str]
) -> tuple[list[Node], list[Edge]]:
    ids = stable_ids(elements)
    nodes = [n for i, e in enumerate(elements) if (n := _clip_node(ids[i], e, media))]
    edges = [
        _clip_edge(ids[i], e, ids)
        for i, e in enumerate(elements)
        if e.type == "connector"
    ]
    return nodes, edges


def _clip_node(nid: str, e: ClipboardElement, media: dict[str, str]) -> Node | None:
    if e.type == "shape" and e.position:
        x, y, w, h = _drawn_box(e)
        return Node(
            id=nid,
            kind=Kind.SHAPE,
            x=x, y=y, w=w, h=h,
            fill=Rgb.from_vector(e.color) if e.fillEnabled else None,
            stroke=Rgb.from_vector(e.strokeColor) or Rgb.from_vector(e.color),
            dashed=e.strokeStyle == STROKE_DASHED,
            stroke_width=float(e.stroke or 1),
            shape_kind=e.shape,
            align=e.alignment if e.alignment in _HALIGN else "center",
            valign=_VALIGN.get(1 if e.verticalAlignment is None else e.verticalAlignment, "middle"),
            font_scale=_scale(e.fontScale),
            adf=_fix_mojibake(e.text),
        )
    if e.type == "text" and e.position:
        size = e.size or Vector2(x=0, y=0)
        return Node(
            id=nid,
            kind=Kind.TEXT,
            x=e.position.x, y=e.position.y, w=size.x, h=size.y,
            color=Rgb.from_vector(e.color),
            align=e.alignment if e.alignment in _HALIGN else "left",
            valign="top",
            font_scale=_scale(e.fontScale),
            auto_width=bool(e.allowFlexibleWidth),
            adf=_fix_mojibake(e.text),
        )
    if e.type == "image" and e.fileId and e.position and e.size:
        return Node(
            id=nid,
            kind=Kind.IMAGE,
            x=e.position.x, y=e.position.y, w=e.size.x, h=e.size.y,
            image=Image(e.fileId, media.get(e.fileId) or f"media/{e.fileId}"),
        )
    if e.type == "path":
        start, end = _point(e.start), _point(e.end)
        if start and end:
            return _line(nid, (start, end), Rgb.from_vector(e.color),
                         e.strokeStyle == STROKE_DASHED, float(e.stroke or 1))
    return None  # connectors become edges; pathWaypoints have no visual of their own


def _clip_edge(eid: str, e: ClipboardElement, ids: list[str]) -> Edge:
    return Edge(
        id=eid,
        source=_index_id(e.sourceIndex, ids),
        target=_index_id(e.targetIndex, ids),
        source_anchor=_anchor(e.sourceAnchor),
        target_anchor=_anchor(e.targetAnchor),
        start=_point(e.start),
        end=_point(e.end),
        start_arrow=e.startCap == CAP_ARROW,
        end_arrow=e.endCap == CAP_ARROW,
        color=Rgb.from_vector(e.color),
        dashed=e.strokeStyle == STROKE_DASHED,
        width=float(e.stroke or 1),
    )


def _drawn_box(e: ClipboardElement) -> Box:
    """Shapes are drawn at basisPosition/basisSize. `size` is not usable: on
    a sample board every shape stores the same 160x160 default, while
    basisSize widths match the drawn widths to within a few px."""
    assert e.position is not None
    if e.basisSize and e.basisSize.x > 0 and e.basisSize.y > 0:
        origin = e.basisPosition or e.position
        return origin.x, origin.y, e.basisSize.x, e.basisSize.y
    size = e.size or Vector2(x=0, y=0)
    return e.position.x, e.position.y, size.x, size.y


def stable_ids(elements: list[ClipboardElement]) -> list[str]:
    """One id per clipboard element, stable across re-extractions.

    The clipboard payload carries no element ids, but each connector names
    the real Yjs ids of the elements it joins, so those are used where known.
    Everything else gets a content hash, so an unchanged element keeps its id
    when other elements are added or removed (array indices would shift).
    """
    n = len(elements)
    known: dict[int, str] = {}
    for e in elements:
        if e.type != "connector":
            continue
        for idx, ref in ((e.sourceIndex, e.sourceElement), (e.targetIndex, e.targetElement)):
            if ref and idx is not None and 0 <= idx < n:
                known.setdefault(idx, ref)

    ids: list[str | None] = [None] * n
    used: set[str] = set()
    hashes: dict[str, int] = defaultdict(int)

    def assign(i: int, key: str) -> None:
        nid = known.get(i)
        if nid is None or nid in used:
            digest = "h:" + hashlib.sha1(key.encode()).hexdigest()[:12]
            hashes[digest] += 1
            nid = digest if hashes[digest] == 1 else f"{digest}.{hashes[digest]}"
        used.add(nid)
        ids[i] = nid

    # Nodes first: connector keys are built from their endpoints' ids.
    for i, e in enumerate(elements):
        if e.type != "connector":
            assign(i, _content_key(e))
    for i, e in enumerate(elements):
        if e.type == "connector":
            src = _index_id(e.sourceIndex, ids)
            tgt = _index_id(e.targetIndex, ids)
            assign(i, f"connector|{src}|{tgt}|{_anchor(e.sourceAnchor)}|{_anchor(e.targetAnchor)}")
    assert all(i is not None for i in ids), "every element is assigned an id"
    return ids  # type: ignore[return-value]


def _content_key(e: ClipboardElement) -> str:
    if e.type == "path":
        return f"path|{_rounded(e.start)}|{_rounded(e.end)}"
    if e.type == "pathWaypoint":
        return f"waypoint|{e.position and (round(e.position.x), round(e.position.y))}"
    return f"{e.type}|{e.shape}|{e.fileId or ''}|{e.text or ''}"


def _index_id(idx: int | None, ids: list[Any]) -> str | None:
    if idx is None or not 0 <= idx < len(ids):
        return None
    return ids[idx]


# ----------------------------------------------------------- fiber adapter


def _from_fiber(fd: FiberDump, media: dict[str, str]) -> tuple[list[Node], list[Edge]]:
    """The Yjs fallback. Element text (`tx`) is an undecoded binary encoding,
    so nodes come out without text; geometry, colour and edges are intact."""
    dims: dict[str, dict[str, Any]] = defaultdict(dict)
    for entry in fd.dimensions:
        prefix, _, eid = str(entry.get("key", "")).partition("#")
        if eid:
            dims[eid][prefix] = entry.get("val")

    in_z = set(fd.zindex)
    order = list(fd.zindex) + [k for k in fd.board if k not in in_z]
    nodes: list[Node] = []
    edges: list[Edge] = []
    for eid in order:
        raw = fd.board.get(eid)
        if not isinstance(raw, dict):
            continue
        t, d = raw.get("t"), dims.get(eid, {})
        if t == "connector":
            edges.append(Edge(
                id=eid,
                source=_str(raw.get("se")),
                target=_str(raw.get("te")),
                source_anchor=_anchor(raw.get("sa")),
                target_anchor=_anchor(raw.get("ta")),
                start_arrow=raw.get("sc") == CAP_ARROW,
                end_arrow=raw.get("ec") == CAP_ARROW,
                color=Rgb.from_float32_be(raw.get("c")),
                dashed=raw.get("sts") == STROKE_DASHED,
                width=_num(raw.get("st"), 1.0),
            ))
            continue
        if t == "shape":
            x, y = _pair(d.get("bp") or d.get("p"))
            w, h = _pair(d.get("bs") or d.get("s"))
            fill_rgb = Rgb.from_float32_be(raw.get("c"))
            nodes.append(Node(
                id=eid, kind=Kind.SHAPE, x=x, y=y, w=w, h=h,
                fill=fill_rgb if raw.get("fe") else None,
                stroke=Rgb.from_float32_be(raw.get("stc")) or fill_rgb,
                dashed=raw.get("sts") == STROKE_DASHED,
                shape_kind=raw.get("sh") if isinstance(raw.get("sh"), int) else None,
                valign=_VALIGN.get(raw.get("va", 1), "middle"),
            ))
        elif t == "text":
            x, y = _pair(d.get("p"))
            w, h = _pair(d.get("s"))
            nodes.append(Node(id=eid, kind=Kind.TEXT, x=x, y=y, w=w, h=h,
                              color=Rgb.from_float32_be(raw.get("c")),
                              align="left", valign="top"))
        elif t == "image" and raw.get("fi"):
            x, y = _pair(d.get("p"))
            w, h = _pair(d.get("s"))
            fid = str(raw["fi"])
            nodes.append(Node(id=eid, kind=Kind.IMAGE, x=x, y=y, w=w, h=h,
                              image=Image(fid, media.get(fid) or f"media/{fid}")))
        # paths keep their points outside `board`; they are not captured
    return nodes, edges


# ------------------------------------------------------------------ layout


@dataclass(frozen=True)
class TextMetrics:
    """Rough text layout model for one renderer; used only to size boxes."""

    char_w: float  # average glyph advance, px
    line_h: float  # line height, px
    pad_w: float  # total horizontal padding inside a node, px
    pad_h: float  # total vertical padding inside a node, px
    para_gap: float  # extra height per blank line, in lines

    def scaled(self, factor: float) -> TextMetrics:
        if factor == 1.0:
            return self
        return replace(self, char_w=self.char_w * factor, line_h=self.line_h * factor)


# Obsidian canvas cards render ~16px text with generous card padding.
CANVAS_METRICS = TextMetrics(char_w=7.5, line_h=22.0, pad_w=32.0, pad_h=36.0, para_gap=0.5)
# The SVG renders 13px system text at line-height 1.35 with 8/12px padding.
SVG_METRICS = TextMetrics(char_w=6.6, line_h=17.6, pad_w=24.0, pad_h=16.0, para_gap=0.15)


def node_box(node: Node, metrics: TextMetrics) -> Box:
    """Where to draw `node` for a renderer with these text metrics.

    Shapes keep their drawn width and wrap text inside it, growing only in
    height (the renderer's font may be larger than Confluence's). Free text
    sizes to its content: its stored size is a placeholder.
    """
    m = metrics.scaled(node.font_scale)
    md = node.markdown
    if node.kind is Kind.TEXT and md:
        tw, th = _unwrapped_bounds(md, m)
        return node.x, node.y, max(node.w, tw), max(node.h, th)
    if node.kind is Kind.SHAPE and md and node.w > 0:
        return node.x, node.y, node.w, max(node.h, _wrapped_height(md, node.w, m))
    return node.x, node.y, node.w, node.h


def layout(board: Board, metrics: TextMetrics) -> dict[str, Box]:
    return {n.id: node_box(n, metrics) for n in board.nodes}


def anchor_point(box: Box, anchor: Point) -> Point:
    x, y, w, h = box
    return x + w * anchor[0], y + h * anchor[1]


def anchor_side(anchor: Point) -> str | None:
    """The box edge an anchor sits on; None for an anchor at the centre."""
    cx, cy = anchor[0] - 0.5, anchor[1] - 0.5
    if max(abs(cx), abs(cy)) < 0.05:
        return None
    if abs(cx) >= abs(cy):
        return "right" if cx > 0 else "left"
    return "bottom" if cy > 0 else "top"


_LIST_PREFIX = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"(\*\*|\*|`|~~)")


def _plain(line: str) -> tuple[str, bool]:
    """Visible text of a markdown line, and whether it is a list item."""
    is_item = bool(_LIST_PREFIX.match(line))
    text = _MD_LINK.sub(r"\1", _LIST_PREFIX.sub("", line))
    return _MD_MARKS.sub("", text).strip(), is_item


def _unwrapped_bounds(markdown: str, m: TextMetrics) -> tuple[float, float]:
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
    usable = max(m.char_w * 4, width - m.pad_w)
    lines = 0.0
    for raw in markdown.splitlines():
        text, is_item = _plain(raw)
        if not text:
            lines += m.para_gap
            continue
        per_line = max(1, int((usable - (3 * m.char_w if is_item else 0)) // m.char_w))
        lines += max(1, math.ceil(len(text) / per_line))
    return lines * m.line_h + m.pad_h


# ----------------------------------------------------------------- helpers


def _line(nid: str, points: tuple[Point, Point], color: Rgb | None, dashed: bool, width: float) -> Node:
    (x1, y1), (x2, y2) = points
    return Node(
        id=nid, kind=Kind.LINE,
        x=min(x1, x2), y=min(y1, y2), w=abs(x2 - x1), h=abs(y2 - y1),
        stroke=color, dashed=dashed, stroke_width=width, points=points,
    )


def _fix_mojibake(s: str | None) -> str | None:
    """Undo UTF-8 text that was decoded as Latin-1 ('â€™' for '’').

    Dumps captured before the probe decoded the clipboard as UTF-8 carry this.
    Text that is not mojibake fails the round trip and is returned unchanged.
    """
    if not s or ("Â" not in s and "â" not in s):
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _anchor(a: Anchor | dict[str, Any] | None) -> Point:
    if a is None:
        return CENTER
    if isinstance(a, dict):
        return _num(a.get("left"), 0.5), _num(a.get("top"), 0.5)
    return float(a.left), float(a.top)


def _point(p: list[float] | None) -> Point | None:
    return (float(p[0]), float(p[1])) if p and len(p) >= 2 else None


def _rounded(p: list[float] | None) -> tuple[int, int] | None:
    pt = _point(p)
    return (round(pt[0]), round(pt[1])) if pt else None


def _pair(v: Any) -> Point:
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return _num(v[0], 0.0), _num(v[1], 0.0)
    return 0.0, 0.0


def _num(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _str(v: Any) -> str | None:
    return str(v) if v else None


def _scale(v: float | None) -> float:
    return v if v and v > 0 else 1.0
