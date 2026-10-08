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
import json
import logging
import math
import re
import struct
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import cache, cached_property
from pathlib import Path
from typing import Any

from .adf import adf_to_html, adf_to_markdown
from .model import Anchor, BoardMeta, ClipboardElement, DumpFile, FiberDump, Vector2, Vector3
from .palette import drawn, section_colours
from .shapes import TextArea, text_area

log = logging.getLogger(__name__)

# Editor enums (docs/confluence-whiteboard-model.md), by stored number.
CAPS = {
    1: "none", 2: "arrow", 3: "filled-arrow", 4: "open-arrow", 5: "filled-diamond",
    6: "open-diamond", 7: "open-circle", 8: "slash", 9: "triple-bar", 10: "open-circle-cross",
    11: "cross", 12: "cross-crows-foot", 13: "crows-foot", 14: "circle-crows-foot",
}
STROKE_STYLES = {0: "none", 1: "solid", 2: "dashed", 3: "dotted"}
SHAPE_OUTLINES = {2: "dashed"}  # a shape's outline is dashed or else solid, "none" and "dotted" included
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
        return None if v is None else cls.of(v.x, v.y, v.z).drawn()

    @classmethod
    def parse(cls, hex_: str) -> Rgb:
        return cls(int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16))

    def drawn(self) -> Rgb:
        """The colour the editor paints for this stored one: boards store the
        legacy palette, the editor draws its current theme's token."""
        return Rgb.parse(drawn(self.hex))

    @classmethod
    def from_float32_be(cls, raw: Any) -> Rgb | None:
        """Decode a Yjs colour, as drawn: see `_float32_rgb`."""
        stored = _float32_rgb(raw)
        return stored.drawn() if stored else None


def _float32_rgb(raw: Any) -> Rgb | None:
    """A Yjs colour as stored: 12 bytes holding three big-endian float32s.

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
    return Rgb.of(r, g, b)


def _channel(v: float) -> int:
    return max(0, min(255, int(round(v))))


class Kind(StrEnum):
    SHAPE = "shape"
    TEXT = "text"  # free-floating text; sizes to its content
    IMAGE = "image"
    LINE = "line"  # freehand line, e.g. a section divider
    ICON = "icon"  # an icon from Atlassian's library; its artwork is not available
    STICKY = "sticky"  # a sticky note: coloured, with text, growing downward to fit it
    SECTION = "section"  # a titled frame; what lies on it is drawn over it


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
    title: str | None = None  # SECTION: its title, drawn on a tab above it (stroke: the tab and border; color: the title)
    icon_key: str | None = None  # ICON: "collection/category/iconId", which its artwork is kept by
    shadow: bool = False  # SECTION: drawn with a drop shadow

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
    id: str = ""  # the label's own element id, if it has one

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
    drawings: dict[int, dict[str, Any]] = field(default_factory=dict)  # icon shapes' artwork, by kind
    icons: dict[str, str] = field(default_factory=dict)  # library icons' SVG files, by icon_key

    def __post_init__(self) -> None:
        self._by_id = {n.id: n for n in self.nodes}

    def node(self, node_id: str | None) -> Node | None:
        return self._by_id.get(node_id) if node_id else None


def load_board(dump_path: Path) -> Board:
    return from_dump(DumpFile.model_validate_json(dump_path.read_text()))


def from_dump(dump: DumpFile, *, report: bool = True) -> Board:
    """The board in a dump; what it holds that cannot be drawn is logged
    once, unless `report` is off."""
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
    losses.icons = sum(1 for n in nodes if n.kind is Kind.ICON and n.icon_key not in dump.icons)
    if report:
        losses.report(dump.board.boardId)
    drawings = {int(k): v for k, v in dump.drawings.items() if k.isdigit()}
    return Board(meta=dump.board, nodes=nodes, edges=edges, drawings=drawings, icons=dict(dump.icons))


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
                id=ids[i],
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


def _shape_outline(value: object, losses: _Losses) -> str:
    """How the editor draws a shape's outline: dashed, or else solid."""
    losses.enum(STROKE_STYLES, value, "stroke style", "solid")
    return SHAPE_OUTLINES.get(value, "solid") if isinstance(value, int) else "solid"


def _clip_node(nid: str, e: ClipboardElement, media: dict[str, str], losses: _Losses) -> Node | None:
    if e.type == "shape" and e.position:
        x, y, w, h = _shape_box(e)
        return Node(
            id=nid,
            kind=Kind.SHAPE,
            x=x, y=y, w=w, h=h,
            fill=Rgb.from_vector(e.color) if e.fillEnabled else None,
            stroke=Rgb.from_vector(e.strokeColor) or Rgb.from_vector(e.color),
            stroke_style=_shape_outline(e.strokeStyle, losses),
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
            auto_width=e.allowFlexibleWidth is not False,  # the editor's default is flexible
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
        return Node(
            id=nid, kind=Kind.ICON, x=x, y=y, w=w, h=h,
            stroke=Rgb.from_vector(e.color),
            icon=_icon_name(e.iconId) or e.category or "icon",
            icon_key=icon_key(e.collection, e.category, e.iconId),
            adf=_fix_mojibake(e.text),
        )
    if e.type == "sticky" and e.position:
        x, y, w, h = _shape_box(e)
        return Node(
            id=nid, kind=Kind.STICKY, x=x, y=y, w=w, h=h,
            fill=Rgb.from_vector(e.color),
            align=e.alignment if e.alignment in _HALIGN else "center",
            valign=_VALIGN.get(1 if e.verticalAlignment is None else e.verticalAlignment, "middle"),
            font_scale=_scale(e.fontScale),
            adf=_fix_mojibake(e.text),
        )
    if e.type == "section" and e.position and e.size:
        stored = Rgb.of(e.color.x, e.color.y, e.color.z) if e.color else None
        return _section(nid, _centred(e.position.x, e.position.y, e.size.x, e.size.y), stored, e.title,
                        bool(e.hasDropShadow))
    return None  # not drawn yet: tables, mind maps, cards, ...


def _section(nid: str, box: Box, stored: Rgb | None, title: str | None, shadow: bool) -> Node:
    """A section: its fill drawn through the palette, its border, tab and
    title in the colours the fill's palette group gives them."""
    border, text = section_colours(stored.hex if stored else "#FFFFFF")
    return Node(id=nid, kind=Kind.SECTION, x=box[0], y=box[1], w=box[2], h=box[3],
                fill=stored.drawn() if stored else Rgb.parse("#FFFFFF"), stroke=Rgb.parse(border),
                color=Rgb.parse(text), title=_fix_mojibake(title) if title else None, shadow=shadow)


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


def icon_key(collection: str | None, category: str | None, icon_id: str | None) -> str | None:
    """How a library icon's artwork is kept: 'aws/storage/Amazon-Simple-Storage-Service'."""
    return f"{collection}/{category}/{icon_id}" if collection and category and icon_id else None


def _icon_name(icon_id: str | None) -> str | None:
    """'Amazon-Simple-Storage-Service' -> 'Amazon Simple Storage Service'."""
    return icon_id.replace("-", " ").replace("_", " ").strip() if icon_id else None


def _image(file_id: str, media: dict[str, str], losses: _Losses) -> Image:
    href = media.get(file_id)
    if href is None:
        losses.missing_images += 1
    return Image(file_id, href)


def _drawn_box(e: ClipboardElement) -> Box:
    """The box the editor draws a text element or icon in."""
    assert e.position is not None
    return _grown_box(_xy(e.position), _xy(e.size), _xy(e.basisPosition), _xy(e.basisSize))


def _shape_box(e: ClipboardElement) -> Box:
    """The box the editor draws a shape in. A shape keeps its basis width
    and grows only downward, to fit its text, so `position` (the centre
    after the last growth) counts only when it moved down; a shift up or
    sideways is stale. The editor does not measure an empty shape at all."""
    assert e.position is not None
    basis_centre, basis_size = _xy(e.basisPosition), _xy(e.basisSize)
    if basis_centre is None or not basis_size or basis_size[0] <= 0 or basis_size[1] <= 0:
        return _drawn_box(e)
    (bx, by), (bw, bh) = basis_centre, basis_size
    grow = 2 * (e.position.y - by) if adf_to_markdown(e.text).strip() else 0.0
    return bx - bw / 2, by - bh / 2, bw, bh + max(grow, 0.0)


def _grown_box(centre: Point, size: Point | None, basis_centre: Point | None, basis_size: Point | None) -> Box:
    """`position` is the centre of the drawn box. The basis box is the box
    before its content grew it; growth keeps one corner fixed, so the drawn
    size is the basis size plus twice the centre's shift. The stored `size`
    is stale (160x160 for every shape examined)."""
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
        if t in ("shape", "text", "sticky"):
            x, y, w, h = _grown_box(_pair(d.get("p")), _opt_pair(d.get("s")), _opt_pair(d.get("bp")),
                                    _opt_pair(d.get("bs")))
        if t == "shape":
            fill_rgb = Rgb.from_float32_be(raw.get("c"))
            nodes.append(Node(
                id=eid, kind=Kind.SHAPE, x=x, y=y, w=w, h=h,
                fill=fill_rgb if raw.get("fe") else None,
                stroke=Rgb.from_float32_be(raw.get("stc")) or fill_rgb,
                stroke_style=_shape_outline(raw.get("sts"), losses),
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
                              auto_width=raw.get("fw") is not False))
        elif t == "sticky":
            nodes.append(Node(id=eid, kind=Kind.STICKY, x=x, y=y, w=w, h=h,
                              fill=Rgb.from_float32_be(raw.get("c")),
                              align=_FIBER_HALIGN.get(raw.get("a", 0), "center"),
                              valign=_VALIGN.get(raw.get("va", 1), "middle"),
                              font_scale=_scale(_num(d.get("fs"), 1.0))))
        elif t == "section":
            raw_rgb = _float32_rgb(raw.get("c"))
            nodes.append(_section(eid, _centred(*_pair(d.get("p")), *_pair(d.get("s"))), raw_rgb,
                                  _str(raw.get("ti")), raw.get("ds") is True))
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


# The editor's whiteboard text (defaultTextSpacing): paragraphs at 11.6/.75
# px on a 22px line; headings at their own sizes (font px, line px) and
# weights. A list item is indented by LIST_INDENT_EM of the font size.
EDITOR_FONT_PX = 11.6 / 0.75
EDITOR_LINE_PX = 22.0
EDITOR_HEADINGS = {1: (27, 32), 2: (23, 27), 3: (18, 23), 4: (16, 23), 5: (14, 18), 6: (13, 18)}
# Weights, from the editor's text widths and drawings: bold is 653, as in
# Atlassian's type scale; the two largest headings are medium.
EDITOR_BOLD_WEIGHT = 653
EDITOR_HEADING_WEIGHTS = {1: 500, 2: 500, 3: 600, 4: 600, 5: 600, 6: 600}
# Space round headings, px: above one unless it comes first, and below one
# before a paragraph or another heading (not before a list). Between two
# blocks only the larger counts, as with CSS margins.
EDITOR_HEADING_ABOVE = {1: 12.0, 2: 11.0, 3: 10.0, 4: 8.5, 5: 7.5, 6: 7.5}
EDITOR_HEADING_BELOW = 6.0
LIST_INDENT_EM = 0.76


@dataclass(frozen=True)
class TextMetrics:
    """Rough text layout model for one renderer; used only to size boxes.

    With `font_px` set, widths are the editor's: each character's width as
    its text engine sets it (text_metrics.json); otherwise every character
    is `char_w` wide. A font scale scales the text after it is set (the
    editor lays scaled text out at its own size, then scales it).
    """

    char_w: float  # average glyph advance, px
    line_h: float  # line height, px
    pad_w: float  # total horizontal padding inside a shape, px
    pad_h: float  # total vertical padding inside a shape, px
    para_gap: float  # extra height per blank line, in lines
    free_pad: float  # total padding round free text, either way, px; not scaled with the font
    font_px: float | None = None  # the editor's font, unscaled
    scale: float = 1.0  # the font scale

    def scaled(self, factor: float) -> TextMetrics:
        if factor == 1.0:
            return self
        return replace(self, char_w=self.char_w * factor, line_h=self.line_h * factor, scale=self.scale * factor)

    def width(self, text: str, bold: bool = False, heading: int = 0) -> float:
        if self.font_px is None:
            return len(text) * self.char_w
        style = f"h{heading}" if heading else "600" if bold else "400"
        return text_width(text, style) * self.font_px / EDITOR_FONT_PX * self.scale

    def line_height(self, heading: int = 0) -> float:
        if heading and self.font_px is not None:
            return self.line_h * EDITOR_HEADINGS[heading][1] / EDITOR_LINE_PX
        return self.line_h

    def gap(self, before: int | None, heading: int, is_item: bool) -> float:
        """The space between a block and the one `before` it (its heading
        level, 0 for none; None if this block comes first)."""
        if before is None or self.font_px is None:
            return 0.0
        below = EDITOR_HEADING_BELOW if before and not is_item else 0.0
        return max(below, EDITOR_HEADING_ABOVE.get(heading, 0.0)) * self.scale

    @property
    def indent(self) -> float:
        return LIST_INDENT_EM * self.font_px * self.scale if self.font_px else 3 * self.char_w


def text_width(text: str, style: str = "400") -> float:
    """How wide the editor sets `text` in a style ("400" paragraph, "600"
    bold, "h1".."h6" headings), at the style's own size: the sum of each
    character's width as the editor's text engine sets it, less kerning."""
    widths, fallback, fit = _width_table()[style]
    return sum(widths.get(ch, fallback) for ch in text) * fit


@cache
def _width_table() -> dict[str, tuple[dict[str, float], float, float]]:
    data = json.loads(Path(__file__).with_name("text_metrics.json").read_text())
    return {style: (widths, sum(widths[c] for c in "abcdefghijklmnopqrstuvwxyz") / 26, data["fit"])
            for style, widths in data["styles"].items()}


# Obsidian canvas cards render ~16px text with generous card padding.
CANVAS_METRICS = TextMetrics(char_w=7.5, line_h=22.0, pad_w=32.0, pad_h=36.0, para_gap=0.5, free_pad=32.0)
# The SVG renders the editor's text in its font, 12px inside a shape and 8px
# round free text, with no space between paragraphs.
SVG_METRICS = TextMetrics(char_w=7.85, line_h=EDITOR_LINE_PX, pad_w=24.0, pad_h=24.0, para_gap=0.0,
                          free_pad=16.0, font_px=EDITOR_FONT_PX)


def node_box(node: Node, metrics: TextMetrics) -> Box:
    """Where to draw `node` for a renderer with these text metrics.

    Shapes keep their drawn width and wrap text inside it, growing only in
    height (the renderer's font may be larger than Confluence's). Free text
    sizes to its content: its stored size is a placeholder.
    """
    m = metrics.scaled(node.font_scale)
    md = node.markdown
    if node.kind is Kind.TEXT and md:
        pad = metrics.free_pad
        if not node.auto_width:  # a fixed width: the text wraps inside it and the box grows down
            return node.x, node.y, node.w, max(node.h, _wrapped_height(md, node.w, m, pad, pad))
        tw, th = _unwrapped_bounds(md, m, pad, pad)
        w, h = max(node.w, tw), max(node.h, th)
        # Text widens away from its aligned edge, as in the editor.
        shift = {"left": 0.0, "center": 0.5, "right": 1.0}.get(node.align, 0.0) * (w - node.w)
        return node.x - shift, node.y, w, h
    if node.kind is Kind.SHAPE and node.w > 0 and (area := text_area(node.shape_kind, node.w)):
        top, height = _grown_shape(node, area, metrics, m, md)
        return node.x, top, node.w, height
    if node.kind is Kind.STICKY and node.w > 0:
        return _sticky_box(node, metrics, m, md)
    if node.kind is Kind.SECTION:  # never smaller than SECTION_MIN either way, about its centre
        w, h = max(node.w, SECTION_MIN), max(node.h, SECTION_MIN)
        return node.x - (w - node.w) / 2, node.y - (h - node.h) / 2, w, h
    if node.kind is Kind.ICON and node.w > 0:  # a square drawing, with any label below it
        top, height = _grown_shape(node, TextArea(node.w, 0.0, node.w, 1.0, 0.0, label_below=True), metrics, m, md)
        return node.x, top, node.w, height
    return node.x, node.y, node.w, node.h


# Stickies (the editor's sticky sizing strategy and constants): text padded
# 12 either way, scaled with the font, in a box of the stored size scaled
# with the font; never smaller than 144 either way. Sections are never
# smaller than 160 (measured on the reference board).
STICKY_PAD = 12.0
STICKY_MIN = 144.0
SECTION_MIN = 160.0


def _sticky_box(node: Node, metrics: TextMetrics, m: TextMetrics, md: str | None) -> Box:
    """A sticky's box as the editor sizes it. Its content box is the stored
    size scaled with the font, less the scaled padding; it grows to fit the
    text downward, and sideways as its text is aligned, from where the
    unscaled box would put it; then the box round it is held to STICKY_MIN
    about its centre."""
    scale = node.font_scale
    pad = STICKY_PAD * scale
    cx, cy = node.x + node.w / 2, node.y + node.h / 2
    width = node.w * scale - 2 * pad
    height = node.h * scale - 2 * pad
    if md and metrics.font_px is not None:
        height = max(height, _wrapped_height(md, width, m, 0.0, 0.0))
    unscaled_w, unscaled_h = node.w - 2 * pad, node.h - 2 * pad
    grow = {"left": 1.0, "right": -1.0}.get(node.align, 0.0)  # left-aligned text grows rightward
    x = cx - grow * (unscaled_w - width) / 2
    y = cy - unscaled_h / 2 + height / 2
    w, h = max(width + 2 * pad, STICKY_MIN), max(height + 2 * pad, STICKY_MIN)
    return x - w / 2, y - h / 2, w, h


def _grown_shape(node: Node, area: TextArea, metrics: TextMetrics, m: TextMetrics, md: str) -> tuple[float, float]:
    """A shape's top and height as the editor sizes it (its sizing strategy):
    first raised to the size one line of text needs, about its centre;
    then its content box (the text, padded 12 and scaled with the font)
    grows downward from there, and the box is rebuilt round it."""
    pad = metrics.pad_w * node.font_scale
    y, h = node.y, node.h
    if md:
        measured = _wrapped_height(md, area.width, m, pad, pad)
    elif area.label_below:
        measured = 0.0  # just the drawing
    else:
        return y, h  # the editor never measures an empty shape
    if md and not area.label_below:  # drawings with a label below keep their own minimum
        least = area.box(m.line_height() + pad)
        if h < least:
            y, h = y - (least - h) / 2, least
    before = area.content(h)
    after = max(before, measured)
    centre = y + h / 2 + area.centre_offset(h) + (after - before) / 2
    grown = area.box(after)
    return centre - area.centre_offset(grown) - grown / 2, grown


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
_HEADING = re.compile(r"^(#{1,6})\s+")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"(\*\*|\*|`|~~)")


def _text_line(raw: str) -> tuple[str, bool, bool, int]:
    """A markdown line's visible text, whether it is a list item, whether
    it is all bold, and its heading level (0 for none)."""
    heading = _HEADING.match(raw)
    body = raw[heading.end():] if heading else raw
    is_item = bool(_LIST_PREFIX.match(body))
    text = _MD_LINK.sub(r"\1", _LIST_PREFIX.sub("", body)).strip()
    bold = len(text) > 4 and text.startswith("**") and text.endswith("**")
    return _MD_MARKS.sub("", text).strip(), is_item, bold, len(heading.group(1)) if heading else 0


def _unwrapped_bounds(markdown: str, m: TextMetrics, pad_w: float | None = None,
                      pad_h: float | None = None) -> tuple[float, float]:
    width, height = 0.0, 0.0
    before: int | None = None
    for raw in markdown.splitlines():
        text, is_item, bold, heading = _text_line(raw)
        if not text:
            height += m.para_gap * m.line_h
            continue
        width = max(width, (m.indent if is_item else 0.0) + m.width(text, bold, heading))
        height += m.gap(before, heading, is_item) + m.line_height(heading)
        before = heading
    return width + _or(pad_w, m.pad_w), height + _or(pad_h, m.pad_h)


def _or(value: float | None, default: float) -> float:
    return default if value is None else value


def _wrapped_height(markdown: str, width: float, m: TextMetrics, pad_w: float | None = None,
                    pad_h: float | None = None) -> float:
    """The height text needs in a box `width` wide, wrapped word by word
    (a word wider than the line breaks anywhere, as the editor's does)."""
    usable = max(m.char_w * 4, width - _or(pad_w, m.pad_w))
    height = 0.0
    before: int | None = None
    for raw in markdown.splitlines():
        text, is_item, bold, heading = _text_line(raw)
        if not text:
            height += m.para_gap * m.line_h
            continue
        lines = _wrapped_lines(text, usable - (m.indent if is_item else 0.0), m, bold, heading)
        height += m.gap(before, heading, is_item) + lines * m.line_height(heading)
        before = heading
    return height + _or(pad_h, m.pad_h)


def _wrapped_lines(text: str, available: float, m: TextMetrics, bold: bool, heading: int) -> int:
    """How many lines `text` takes in `available` width, broken as the
    editor's text engine breaks it: a line holds a word only if the space
    after it fits too, a line may also break after a hyphen, and a word
    wider than the line breaks anywhere."""
    available = max(available, 1.0)
    space = m.width(" ", bold, heading)
    words = text.split()
    lines, current = 1, 0.0
    for i, word in enumerate(words):
        parts = _HYPHENATED.findall(word)
        for n, part in enumerate(parts):
            before = 0.0 if n else space
            after = space if n == len(parts) - 1 and i < len(words) - 1 else 0.0
            lines, current = _place(part, before, after, available, m, bold, heading, lines, current)
    return lines


_HYPHENATED = re.compile(r"[^-]*-+|[^-]+")


def _place(word: str, before: float, after: float, available: float, m: TextMetrics, bold: bool,
           heading: int, lines: int, current: float) -> tuple[int, float]:
    """Set `word` (with the space `before` it, needing room for the space
    `after` it) on the last of `lines`, or start a new line."""
    w = m.width(word, bold, heading)
    if current and current + before + w + after <= available:
        return lines, current + before + w
    if current:
        lines += 1
    extra = max(0, math.ceil(w / available) - 1)
    return lines + extra, w - extra * available


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
