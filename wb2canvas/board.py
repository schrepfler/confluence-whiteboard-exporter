"""Renderer-independent model of a whiteboard.

A dump holds one of two raw captures: the canvas's own copy/paste payload
(`clipboard`) or the Yjs document read out of the page (`fiber`).
`from_dump` normalises either into a `Board`, so each renderer is written
once against one model.

Node geometry is the source's own: the boxes Confluence draws (it stores
centres; the model holds top-left corners). Renderers call `layout()` to
adapt those boxes to their text metrics.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import struct
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from typing import Any

from .adf import adf_to_html, adf_to_markdown
from .model import Anchor, BoardMeta, ClipboardElement, DumpFile, FiberDump, Vector2, Vector3

log = logging.getLogger(__name__)

# Editor enums (docs/confluence-whiteboard-model.md), by stored number.
CAPS = {
    1: "none", 2: "arrow", 3: "filled-arrow", 4: "open-arrow", 5: "filled-diamond",
    6: "open-diamond", 7: "open-circle", 8: "slash", 9: "triple-bar", 10: "open-circle-cross",
    11: "cross", 12: "cross-crows-foot", 13: "crows-foot", 14: "circle-crows-foot",
}
STROKE_STYLES = {0: "none", 1: "solid", 2: "dashed", 3: "dotted"}
ROUTINGS = {1: "straight", 2: "dynamic", 3: "curved"}
_VALIGN = {0: "top", 1: "middle", 2: "bottom"}  # observed: shapes use 1 (middle)
_HALIGN = frozenset({"left", "center", "right"})
_FIBER_HALIGN = {0: "center", 1: "left", 2: "right"}
_AXES = {0: "x", 1: "y"}  # pathWaypoint axis: the segment a right-angled handle pins
LABEL_SIDES = {0: "centre", 1: "left", 2: "right"}

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
    ICON = "icon"  # an icon from Atlassian's library; its artwork is not available


@dataclass(frozen=True)
class Image:
    file_id: str
    href: str | None  # relative to the dump's directory, e.g. "media/<id>.jpg"; None if not downloaded


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
    stroke_style: str = "solid"  # a STROKE_STYLES value
    stroke_width: float = 1.0
    shape_kind: int | None = None
    align: str = "center"
    valign: str = "middle"
    font_scale: float = 1.0
    auto_width: bool = False  # free text that never wraps
    adf: str | None = None  # Atlassian Document Format, as JSON text
    image: Image | None = None
    points: tuple[Point, ...] = ()  # LINE endpoints
    icon: str | None = None  # ICON: the library icon's name

    @cached_property
    def markdown(self) -> str | None:
        return (adf_to_markdown(self.adf) or None) if self.adf else None

    @cached_property
    def html(self) -> str:
        return adf_to_html(self.adf) if self.adf else ""


@dataclass(frozen=True)
class Label:
    """Text on a connector, at `proportion` of the way along it."""

    adf: str
    color: Rgb | None = None
    font_scale: float = 1.0
    proportion: float = 0.5
    side: str = "centre"  # on the line, or beside it ("left"/"right" of travel)

    @cached_property
    def markdown(self) -> str:
        return adf_to_markdown(self.adf) or ""

    @cached_property
    def html(self) -> str:
        return adf_to_html(self.adf)


@dataclass(eq=False)
class Edge:
    id: str
    source: str | None  # node id; None when the end is not on the board
    target: str | None
    source_anchor: Point = CENTER  # fraction of the node box (left, top)
    target_anchor: Point = CENTER
    start: Point | None = None  # recorded endpoint, used when source is None
    end: Point | None = None
    start_cap: str = "none"  # a CAPS value
    end_cap: str = "none"
    routing: str = "curved"  # a ROUTINGS value
    waypoints: tuple[Point, ...] = ()  # bend points, in order
    waypoint_axes: tuple[str | None, ...] = ()  # right-angled: "x"/"y" segment each pins
    labels: tuple[Label, ...] = ()
    color: Rgb | None = None
    stroke_style: str = "solid"
    stroke_size: int = 1  # 1 small, 2 medium, 3 large


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
    losses = _Losses()
    if dump.strategy == "fiber" and dump.fiber_dump is not None:
        nodes, edges = _from_fiber(dump.fiber_dump, dump.media, losses)
    else:
        nodes, edges = _from_clipboard(dump.elements, dump.media, losses)
    node_ids = {n.id for n in nodes}
    for e in edges:  # an end that is not a drawn node falls back to its point
        if e.source not in node_ids:
            e.source = None
        if e.target not in node_ids:
            e.target = None
    losses.report(dump.board.boardId)
    return Board(meta=dump.board, nodes=nodes, edges=edges)


@dataclass
class _Losses:
    """What a dump holds that the model cannot represent, reported once."""

    elements: Counter[str] = field(default_factory=Counter)
    values: set[str] = field(default_factory=set)
    missing_images: int = 0
    icons: int = 0

    def enum(self, table: dict[int, str], value: Any, what: str, default: str) -> str:
        if value is None:
            return default
        name = table.get(value) if isinstance(value, int) else None
        if name is None:
            self.values.add(f"{what} {value!r}")
            return default
        return name

    def report(self, board_id: str) -> None:
        if self.elements:
            omitted = ", ".join(f"{n} {t}" for t, n in sorted(self.elements.items()))
            log.warning("board %s: omitted elements it cannot draw: %s", board_id, omitted)
        if self.values:
            log.warning("board %s: unknown %s; drawn with defaults", board_id, ", ".join(sorted(self.values)))
        if self.icons:
            log.warning("board %s: %d library icon(s) drawn as placeholders (their artwork is not available)",
                        board_id, self.icons)
        if self.missing_images:
            log.warning("board %s: %d image(s) were not downloaded; drawn as placeholders",
                        board_id, self.missing_images)


# ------------------------------------------------------- clipboard adapter


def _from_clipboard(
    elements: list[ClipboardElement], media: dict[str, str], losses: _Losses
) -> tuple[list[Node], list[Edge]]:
    ids = stable_ids(elements)
    media = _with_shared_pictures(media, ((e.fileId, e.imageHash) for e in elements if e.type == "image"))
    nodes: list[Node] = []
    edges: dict[int, Edge] = {}
    bends: dict[int, list[tuple[float, Point, str | None]]] = defaultdict(list)
    labels: dict[int, list[Label]] = defaultdict(list)
    for i, e in enumerate(elements):
        if e.type == "connector":
            edges[i] = _clip_edge(ids[i], e, ids, losses)
        elif e.type == "pathWaypoint" and e.position and e.sourcePathIndex is not None:
            bends[e.sourcePathIndex].append((e.order or 0.0, (e.position.x, e.position.y), _AXES.get(e.axis)))
        elif e.type == "pathLabel" and e.sourcePathIndex is not None and e.text:
            labels[e.sourcePathIndex].append(Label(
                adf=_fix_mojibake(e.text) or "",
                color=Rgb.from_vector(e.color),
                font_scale=_scale(e.fontScale),
                proportion=e.proportion if e.proportion is not None else 0.5,
                side=losses.enum(LABEL_SIDES, e.pathOffsetPosition, "label side", "centre"),
            ))
        elif node := _clip_node(ids[i], e, media, losses):
            nodes.append(node)
        else:
            losses.elements[e.type] += 1
    _attach_waypoints(edges, bends, losses)
    for key, items in labels.items():
        if (edge := edges.get(key)) is None:
            losses.elements["pathLabel"] += len(items)
        else:
            edge.labels = tuple(items)
    return nodes, list(edges.values())


def _attach_waypoints(edges: dict[Any, Edge], bends: dict[Any, list[tuple[float, Point, str | None]]], losses: _Losses) -> None:
    for key, points in bends.items():
        if (edge := edges.get(key)) is None:
            losses.elements["pathWaypoint"] += len(points)
        else:
            ordered = sorted(points, key=lambda b: b[0])
            edge.waypoints = tuple(p for _, p, _ in ordered)
            edge.waypoint_axes = tuple(a for _, _, a in ordered)


def _clip_node(nid: str, e: ClipboardElement, media: dict[str, str], losses: _Losses) -> Node | None:
    if e.type == "shape" and e.position:
        x, y, w, h = _drawn_box(e)
        return Node(
            id=nid,
            kind=Kind.SHAPE,
            x=x, y=y, w=w, h=h,
            fill=Rgb.from_vector(e.color) if e.fillEnabled else None,
            stroke=Rgb.from_vector(e.strokeColor) or Rgb.from_vector(e.color),
            stroke_style=losses.enum(STROKE_STYLES, e.strokeStyle, "stroke style", "solid"),
            stroke_width=float(e.stroke or 1),
            shape_kind=e.shape,
            align=e.alignment if e.alignment in _HALIGN else "center",
            valign=_VALIGN.get(1 if e.verticalAlignment is None else e.verticalAlignment, "middle"),
            font_scale=_scale(e.fontScale),
            adf=_fix_mojibake(e.text),
        )
    if e.type == "text" and e.position:
        x, y, w, h = _drawn_box(e)
        return Node(
            id=nid,
            kind=Kind.TEXT,
            x=x, y=y, w=w, h=h,
            color=Rgb.from_vector(e.color),
            align=e.alignment if e.alignment in _HALIGN else "left",
            valign="top",
            font_scale=_scale(e.fontScale),
            auto_width=bool(e.allowFlexibleWidth),
            adf=_fix_mojibake(e.text),
        )
    if e.type == "image" and e.fileId and e.position and e.size:
        x, y, w, h = _centred(e.position.x, e.position.y, e.size.x, e.size.y)
        return Node(
            id=nid,
            kind=Kind.IMAGE,
            x=x, y=y, w=w, h=h,
            image=_image(e.fileId, media, losses),
        )
    if e.type == "path":
        start, end = _point(e.start), _point(e.end)
        if start and end:
            style = losses.enum(STROKE_STYLES, e.strokeStyle, "stroke style", "solid")
            return _line(nid, (start, end), Rgb.from_vector(e.color), style, float(e.stroke or 1))
    if e.type == "advanced-icon" and e.position:
        x, y, w, h = _drawn_box(e)
        losses.icons += 1
        return Node(
            id=nid, kind=Kind.ICON, x=x, y=y, w=w, h=h,
            stroke=Rgb.from_vector(e.color),
            icon=_icon_name(e.iconId) or e.category or "icon",
            adf=_fix_mojibake(e.text),
        )
    return None  # not drawn yet: stickies, sections, tables, ...


def _clip_edge(eid: str, e: ClipboardElement, ids: list[str], losses: _Losses) -> Edge:
    return Edge(
        id=eid,
        source=_index_id(e.sourceIndex, ids),
        target=_index_id(e.targetIndex, ids),
        source_anchor=_anchor(e.sourceAnchor),
        target_anchor=_anchor(e.targetAnchor),
        start=_point(e.start),
        end=_point(e.end),
        start_cap=losses.enum(CAPS, e.startCap, "line end", "none"),
        end_cap=losses.enum(CAPS, e.endCap, "line end", "none"),
        routing=losses.enum(ROUTINGS, e.presentation, "routing", "curved"),
        color=Rgb.from_vector(e.color),
        stroke_style=losses.enum(STROKE_STYLES, e.strokeStyle, "stroke style", "solid"),
        stroke_size=int(e.stroke or 1),
    )


def _with_shared_pictures(media: dict[str, str], images: Iterable[tuple[str | None, str | None]]) -> dict[str, str]:
    """The canvas loads each picture once, so only one of several images
    showing the same picture (same hash) may have been downloaded; the
    others reuse its file."""
    images = [(f, h) for f, h in images if f]
    by_hash = {h: media[f] for f, h in images if h and f in media}
    return {**{f: by_hash[h] for f, h in images if f not in media and h in by_hash}, **media}


def _icon_name(icon_id: str | None) -> str | None:
    """'Amazon-Simple-Storage-Service' -> 'Amazon Simple Storage Service'."""
    return icon_id.replace("-", " ").replace("_", " ").strip() if icon_id else None


def _image(file_id: str, media: dict[str, str], losses: _Losses) -> Image:
    href = media.get(file_id)
    if href is None:
        losses.missing_images += 1
    return Image(file_id, href)


def _drawn_box(e: ClipboardElement) -> Box:
    """The box the editor draws a shape or text element in."""
    assert e.position is not None
    return _grown_box(_xy(e.position), _xy(e.size), _xy(e.basisPosition), _xy(e.basisSize))


def _grown_box(centre: Point, size: Point | None, basis_centre: Point | None, basis_size: Point | None) -> Box:
    """`position` is the centre of the drawn box. The basis box is the box
    before its content grew it; growth keeps one corner fixed, so the drawn
    size is the basis size plus twice the centre's shift. The stored `size`
    is stale (160x160 for every shape on a sample board)."""
    cx, cy = centre
    if basis_size and basis_size[0] > 0 and basis_size[1] > 0:
        bx, by = basis_centre or centre
        w, h = basis_size[0] + 2 * abs(cx - bx), basis_size[1] + 2 * abs(cy - by)
    else:
        w, h = size or (0.0, 0.0)
    return _centred(cx, cy, w, h)


def _centred(cx: float, cy: float, w: float, h: float) -> Box:
    return cx - w / 2, cy - h / 2, w, h


def _xy(v: Vector2 | None) -> Point | None:
    return (v.x, v.y) if v is not None else None


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


def _from_fiber(fd: FiberDump, media: dict[str, str], losses: _Losses) -> tuple[list[Node], list[Edge]]:
    """The Yjs fallback. Element text (`tx`) is an undecoded binary encoding,
    so nodes come out without text; geometry, colour and edges are intact."""
    dims: dict[str, dict[str, Any]] = defaultdict(dict)
    for entry in fd.dimensions:
        prefix, _, eid = str(entry.get("key", "")).partition("#")
        if eid:
            dims[eid][prefix] = entry.get("val")

    media = _with_shared_pictures(media, (
        (str(v.get("fi")), v.get("ih")) for v in fd.board.values() if isinstance(v, dict) and v.get("t") == "image"
    ))
    in_z = set(fd.zindex)
    order = list(fd.zindex) + [k for k in fd.board if k not in in_z]
    nodes: list[Node] = []
    edges: dict[str, Edge] = {}
    bends: dict[str, list[tuple[float, Point, str | None]]] = defaultdict(list)
    for eid in order:
        raw = fd.board.get(eid)
        if not isinstance(raw, dict):
            continue
        t, d = raw.get("t"), dims.get(eid, {})
        if t == "pathWaypoint" and raw.get("pi") and (d.get("bp") or d.get("p")):
            bends[str(raw["pi"])].append((_num(raw.get("or"), 0.0), _pair(d.get("bp") or d.get("p")),
                                          _AXES.get(raw.get("ax"))))
            continue
        if t == "connector":
            edges[eid] = Edge(
                id=eid,
                source=_str(raw.get("se")),
                target=_str(raw.get("te")),
                source_anchor=_anchor(raw.get("sa")),
                target_anchor=_anchor(raw.get("ta")),
                start_cap=losses.enum(CAPS, raw.get("sc"), "line end", "none"),
                end_cap=losses.enum(CAPS, raw.get("ec"), "line end", "none"),
                routing=losses.enum(ROUTINGS, raw.get("pr"), "routing", "curved"),
                color=Rgb.from_float32_be(raw.get("c")),
                stroke_style=losses.enum(STROKE_STYLES, raw.get("sts"), "stroke style", "solid"),
                stroke_size=int(_num(raw.get("st"), 1.0)),
            )
            continue
        if t in ("shape", "text"):
            x, y, w, h = _grown_box(_pair(d.get("p")), _opt_pair(d.get("s")), _opt_pair(d.get("bp")),
                                    _opt_pair(d.get("bs")))
        if t == "shape":
            fill_rgb = Rgb.from_float32_be(raw.get("c"))
            nodes.append(Node(
                id=eid, kind=Kind.SHAPE, x=x, y=y, w=w, h=h,
                fill=fill_rgb if raw.get("fe") else None,
                stroke=Rgb.from_float32_be(raw.get("stc")) or fill_rgb,
                stroke_style=losses.enum(STROKE_STYLES, raw.get("sts"), "stroke style", "solid"),
                stroke_width=_num(raw.get("st"), 1.0),
                shape_kind=raw.get("sh") if isinstance(raw.get("sh"), int) else None,
                align=_FIBER_HALIGN.get(raw.get("a", 0), "center"),
                valign=_VALIGN.get(raw.get("va", 1), "middle"),
                font_scale=_scale(_num(d.get("fs"), 1.0)),
            ))
        elif t == "text":
            nodes.append(Node(id=eid, kind=Kind.TEXT, x=x, y=y, w=w, h=h,
                              color=Rgb.from_float32_be(raw.get("c")),
                              align=_FIBER_HALIGN.get(raw.get("a", 1), "left"), valign="top",
                              font_scale=_scale(_num(d.get("fs"), 1.0)),
                              auto_width=bool(raw.get("fw"))))
        elif t == "image" and raw.get("fi"):
            x, y, w, h = _centred(*_pair(d.get("p")), *_pair(d.get("s")))
            fid = str(raw["fi"])
            nodes.append(Node(id=eid, kind=Kind.IMAGE, x=x, y=y, w=w, h=h,
                              image=_image(fid, media, losses)))
        else:
            # Includes paths: their points live outside `board` and are not captured.
            losses.elements[str(t)] += 1
    _attach_waypoints(edges, bends, losses)
    return nodes, list(edges.values())


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
        w, h = max(node.w, tw), max(node.h, th)
        # Text widens away from its aligned edge, as in the editor.
        shift = {"left": 0.0, "center": 0.5, "right": 1.0}.get(node.align, 0.0) * (w - node.w)
        return node.x - shift, node.y, w, h
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


def _line(nid: str, points: tuple[Point, Point], color: Rgb | None, style: str, width: float) -> Node:
    (x1, y1), (x2, y2) = points
    return Node(
        id=nid, kind=Kind.LINE,
        x=min(x1, x2), y=min(y1, y2), w=abs(x2 - x1), h=abs(y2 - y1),
        stroke=color, stroke_style=style, stroke_width=width, points=points,
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
    return _opt_pair(v) or (0.0, 0.0)


def _opt_pair(v: Any) -> Point | None:
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return _num(v[0], 0.0), _num(v[1], 0.0)
    return None


def _num(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _str(v: Any) -> str | None:
    return str(v) if v else None


def _scale(v: float | None) -> float:
    return v if v and v > 0 else 1.0
