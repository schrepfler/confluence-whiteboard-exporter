"""Comment threads pinned on a board.

A comment is two things kept apart. Its pin is an element of the board's
Yjs document (`t: "comment"`, a 32 square at `p`, `ci` naming its thread,
`at` the element it is pinned to as {parentId, offset from that element's
top-left}, `r` whether it is resolved); the clipboard copy leaves pins out.
Its thread is Confluence content: inline comments on the whiteboard, whose
`markerRef` is the pin's `ci`, with replies under them.

A clipboard dump is in the copy's own frame, which the copy translates by
a constant; pins are moved into that frame, found as the offset that the
boxed elements of both agree on.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

from .model import ClipboardElement, CommentPin, CommentPost, FiberDump

log = logging.getLogger(__name__)

# Types with a centre and a size in both the copy and the Yjs document.
_BOXED = frozenset({"shape", "text", "sticky", "section", "image", "sticker", "stamp", "advanced-icon"})


def read_pins(fd: FiberDump, elements: list[ClipboardElement] | None = None) -> list[CommentPin]:
    """The board's comment pins, without their threads. With `elements` (a
    clipboard dump), in the copy's frame, pinned to an element by its index
    in the copy; otherwise pinned by Yjs id."""
    dims = _dims(fd)
    pins = [(eid, raw) for eid, raw in fd.board.items() if isinstance(raw, dict) and raw.get("t") == "comment"]
    if not pins:
        return []
    shift = (0.0, 0.0)
    if elements is not None:
        found = _shift(fd, dims, elements)
        if found is None:
            log.warning("could not place %d comment pin(s) on the copy of the board; left out", len(pins))
            return []
        shift = found
    out = []
    for eid, raw in pins:
        p = dims.get(eid, {}).get("p")
        if not _is_pair(p):
            continue
        at = raw.get("at") if isinstance(raw.get("at"), dict) else {}
        parent = str(at["parentId"]) if at.get("parentId") else None
        pin = CommentPin(id=eid, marker=_str(raw.get("ci")), x=p[0] - shift[0], y=p[1] - shift[1],
                         resolved=raw.get("r") is True)
        if parent and elements is None:
            pin.parentId = parent
        elif parent and elements is not None:
            pin.parentIndex = _index_of(fd.board.get(parent), dims.get(parent, {}), shift, elements)
        out.append(pin)
    return out


def _shift(fd: FiberDump, dims: dict[str, dict[str, Any]], elements: list[ClipboardElement]) -> tuple[float, float] | None:
    """How far the copy moved the board: the offset most boxed elements of
    the same type and size agree on."""
    votes: Counter[tuple[float, float]] = Counter()
    for eid, raw in fd.board.items():
        t = raw.get("t") if isinstance(raw, dict) else None
        p, s = dims.get(eid, {}).get("p"), dims.get(eid, {}).get("s")
        if t not in _BOXED or not (_is_pair(p) and _is_pair(s)):
            continue
        for e in elements:
            if e.type == t and e.position and e.size and _near(e.size.x, s[0]) and _near(e.size.y, s[1]):
                votes[(round(p[0] - e.position.x, 2), round(p[1] - e.position.y, 2))] += 1
    if not votes:
        return None
    (best, n), *rest = votes.most_common(2) + [((0.0, 0.0), 0)]
    return best if n > rest[0][1] else None


def _index_of(raw: Any, d: dict[str, Any], shift: tuple[float, float],
              elements: list[ClipboardElement]) -> int | None:
    """The copy's index of a Yjs element: the one of its type at its place."""
    p, s = d.get("p"), d.get("s")
    if not isinstance(raw, dict) or not (_is_pair(p) and _is_pair(s)):
        return None
    x, y = p[0] - shift[0], p[1] - shift[1]
    for i, e in enumerate(elements):
        if (e.type == raw.get("t") and e.position and e.size and _near(e.position.x, x) and _near(e.position.y, y)
                and _near(e.size.x, s[0]) and _near(e.size.y, s[1])):
            return i
    return None


def attach_threads(pins: list[CommentPin], comments: list[dict[str, Any]]) -> None:
    """Fill each pin's posts from the board's inline comments (the REST
    API's content, with body.atlas_doc_format, history,
    extensions.inlineProperties and ancestors expanded): its thread's first
    comment, then the replies, oldest first. Resolution is the thread's."""
    by_marker: dict[str, dict[str, Any]] = {}
    replies: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in comments:
        ancestors = [a.get("id") for a in c.get("ancestors") or [] if a.get("type", "comment") == "comment"]
        if ancestors:
            replies[str(ancestors[0])].append(c)
        elif marker := ((c.get("extensions") or {}).get("inlineProperties") or {}).get("markerRef"):
            by_marker[marker] = c
    for pin in pins:
        root = by_marker.get(pin.marker or "")
        if root is None:
            continue
        thread = [root, *sorted(replies.get(str(root.get("id")), []), key=_created)]
        pin.posts = [_post(c) for c in thread]
        status = ((root.get("extensions") or {}).get("resolution") or {}).get("status")
        if status in ("resolved", "open"):
            pin.resolved = status == "resolved"
    missing = sum(1 for p in pins if not p.posts)
    if missing:
        log.warning("%d comment pin(s) have no thread the API returned; drawn without text", missing)


def avatar_paths(comments: list[dict[str, Any]]) -> dict[str, str]:
    """Each author's own profile picture (not a default one), by account id."""
    out = {}
    for c in comments:
        by = (c.get("history") or {}).get("createdBy") or {}
        pic = by.get("profilePicture") or {}
        if by.get("accountId") and pic.get("path") and not pic.get("isDefault"):
            out[str(by["accountId"])] = str(pic["path"])
    return out


def _post(c: dict[str, Any]) -> CommentPost:
    by = (c.get("history") or {}).get("createdBy") or {}
    body = ((c.get("body") or {}).get("atlas_doc_format") or {}).get("value")
    return CommentPost(id=str(c.get("id")), author=_str(by.get("displayName") or by.get("publicName")),
                       authorId=_str(by.get("accountId")), created=_created(c) or None, body=_str(body))


def _created(c: dict[str, Any]) -> str:
    return str((c.get("history") or {}).get("createdDate") or "")


def _dims(fd: FiberDump) -> dict[str, dict[str, Any]]:
    dims: dict[str, dict[str, Any]] = defaultdict(dict)
    for entry in fd.dimensions:
        prefix, _, eid = str(entry.get("key", "")).partition("#")
        if eid:
            dims[eid][prefix] = entry.get("val")
    return dims


def _is_pair(v: Any) -> bool:
    return isinstance(v, list | tuple) and len(v) == 2 and all(isinstance(n, int | float) for n in v)


def _near(a: float, b: float) -> bool:
    return abs(a - b) < 0.01


def _str(v: Any) -> str | None:
    return str(v) if v not in (None, "") else None
