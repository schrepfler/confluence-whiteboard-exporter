from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


DUMP_VERSION = 1


class BoardMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    boardId: str
    contentId: str | None = None
    title: str
    spaceKey: str
    parentId: str | None = None
    parentType: str | None = None
    ownerId: str | None = None
    createdAt: str | None = None


class Vector2(BaseModel):
    model_config = ConfigDict(extra="allow")

    x: float
    y: float
    type: Literal["Vector2"] = "Vector2"


class Vector3(BaseModel):
    model_config = ConfigDict(extra="allow")

    x: float
    y: float
    z: float
    type: Literal["Vector3"] = "Vector3"


class Anchor(BaseModel):
    model_config = ConfigDict(extra="allow")

    left: float
    top: float


class ClipboardElement(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    source: int | None = None
    position: Vector2 | None = None
    size: Vector2 | None = None
    color: Vector3 | None = None
    strokeColor: Vector3 | None = None
    strokeStyle: int | None = None
    text: str | None = None
    shape: int | None = None
    fillEnabled: bool | None = None
    fontScale: float | None = None
    basisSize: Vector2 | None = None
    basisPosition: Vector2 | None = None
    alignment: str | None = None
    verticalAlignment: int | None = None
    rotation: float | None = None

    sourceElement: str | None = None
    sourceAnchor: Anchor | None = None
    sourceIndex: int | None = None
    targetElement: str | None = None
    targetAnchor: Anchor | None = None
    targetIndex: int | None = None
    startCap: int | None = None
    endCap: int | None = None
    presentation: int | None = None
    stroke: int | None = None
    start: list[float] | None = None
    end: list[float] | None = None
    segments: list[Any] | None = None

    fileId: str | None = None
    mimeType: str | None = None
    nativeSize: Vector2 | None = None
    imageHash: str | None = None
    isTransparent: bool | None = None
    imageDataIndex: int | None = None


class FiberDump(BaseModel):
    model_config = ConfigDict(extra="allow")

    board: dict[str, Any]
    dimensions: list[dict[str, Any]]
    zindex: list[str]
    meta: dict[str, Any] | None = None


class DumpFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dump_version: int = DUMP_VERSION
    board: BoardMeta
    strategy: Literal["clipboard", "fiber"]
    elements: list[ClipboardElement] = Field(default_factory=list)
    fiber_dump: FiberDump | None = None
    media: dict[str, str] = Field(default_factory=dict)


CanvasColor = str


class CanvasNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["text", "file", "link", "group"]
    x: int
    y: int
    width: int
    height: int
    color: CanvasColor | None = None
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
    fromSide: Literal["top", "right", "bottom", "left"] | None = None
    toSide: Literal["top", "right", "bottom", "left"] | None = None
    fromEnd: Literal["none", "arrow"] | None = None
    toEnd: Literal["none", "arrow"] | None = None
    color: CanvasColor | None = None
    label: str | None = None


class CanvasDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[CanvasNode] = Field(default_factory=list)
    edges: list[CanvasEdge] = Field(default_factory=list)
