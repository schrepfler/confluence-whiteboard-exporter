from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from confluence_whiteboard_exporter.board import from_dump
from confluence_whiteboard_exporter.canvas import render_canvas
from confluence_whiteboard_exporter.comments import attach_threads, avatar_paths, read_pins
from confluence_whiteboard_exporter.model import ClipboardElement, CommentPin, DumpFile, FiberDump
from confluence_whiteboard_exporter.svg import render_svg

META = {"boardId": "1", "title": "t", "spaceKey": "S"}
SHIFT = (585.0, -887.0)  # how far the copy below moved the board


def _fiber() -> FiberDump:
    """A section with a pin on it (resolved), and a pin on its own, as the
    Yjs document holds them: a pinned one at its parent's top-left plus its
    offset."""
    return FiberDump.model_validate({
        "board": {
            "S": {"t": "section", "ti": "Plan"},
            "K": {"t": "shape"},
            "P1": {"t": "comment", "ci": "m-1", "at": None, "r": False},
            "P2": {"t": "comment", "ci": "m-2", "at": {"parentId": "S", "offset": [380, 20]}, "r": True},
        },
        "dimensions": [
            {"key": "p#S", "val": [200, 100]}, {"key": "s#S", "val": [400, 300]},
            {"key": "p#K", "val": [700, 100]}, {"key": "s#K", "val": [160, 80]},
            {"key": "p#P1", "val": [900, -50]}, {"key": "s#P1", "val": [32, 32]},
            {"key": "p#P2", "val": [380, -30]}, {"key": "s#P2", "val": [32, 32]},
        ],
        "zindex": ["K", "S", "P1", "P2"],
    })


def _copy() -> list[ClipboardElement]:
    """The same board copied: translated, and in another order."""
    def at(x: float, y: float) -> dict:
        return {"x": x - SHIFT[0], "y": y - SHIFT[1]}
    return [ClipboardElement.model_validate(e) for e in (
        {"type": "section", "position": at(200, 100), "size": {"x": 400, "y": 300}, "title": "Plan"},
        {"type": "connector"},
        {"type": "shape", "position": at(700, 100), "size": {"x": 160, "y": 80}},
    )]


def _rest(cid: str, marker: str | None, text: str, created: str, *, under: str | None = None,
          status: str = "open", default_picture: bool = False) -> dict:
    ext = {"location": "inline", "inlineProperties": {"markerRef": marker, "originalSelection": ""},
           "resolution": {"status": status}}
    return {
        "id": cid, "type": "comment", "extensions": ext,
        "ancestors": [{"id": under, "type": "comment"}] if under else [],
        "history": {"createdDate": created, "createdBy": {
            "accountId": "acc-1", "displayName": "Ada Lovelace",
            "profilePicture": {"path": "/wiki/aa-avatar/acc-1", "isDefault": default_picture}}},
        "body": {"atlas_doc_format": {"value": json.dumps(
            {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]})}},
    }


def test_pins_in_a_fiber_dump_keep_their_place_and_parent_id() -> None:
    free, on = read_pins(_fiber())
    assert (free.id, free.marker, free.x, free.y, free.parentId, free.resolved) == ("P1", "m-1", 900, -50, None, False)
    assert (on.parentId, on.parentIndex, on.resolved) == ("S", None, True)


def test_pins_move_into_the_copy_and_find_their_parent_in_it() -> None:
    free, on = read_pins(_fiber(), _copy())
    assert (free.x, free.y) == (900 - SHIFT[0], -50 - SHIFT[1])
    assert (on.parentIndex, on.parentId) == (0, None), "the section, by its index in the copy"


def test_pins_are_left_out_when_the_copy_cannot_be_matched() -> None:
    assert read_pins(_fiber(), []) == []


def test_threads_are_matched_by_marker_with_replies_oldest_first() -> None:
    pins = read_pins(_fiber())
    attach_threads(pins, [
        _rest("2", None, "second", "2026-10-29T12:50:00Z", under="1"),
        _rest("1", "m-1", "first", "2026-10-29T12:46:00Z"),
        _rest("3", None, "reply", "2026-10-29T12:48:00Z", under="1"),
        _rest("4", "m-2", "done", "2026-10-29T12:40:00Z", status="open"),
    ])
    free, on = pins
    assert [p.id for p in free.posts] == ["1", "3", "2"]
    assert (free.posts[0].author, free.posts[0].created) == ("Ada Lovelace", "2026-10-29T12:46:00Z")
    assert on.resolved is False, "the thread's own resolution wins over the pin's flag"


def test_only_real_profile_pictures_are_saved() -> None:
    assert avatar_paths([_rest("1", "m", "x", "t")]) == {"acc-1": "/wiki/aa-avatar/acc-1"}
    assert avatar_paths([_rest("1", "m", "x", "t", default_picture=True)]) == {}


def _board(**pin: object):
    section = {"type": "section", "position": {"x": 0, "y": 0}, "size": {"x": 400, "y": 300}, "title": "Plan"}
    posts = [{"id": "1", "author": "Ada Lovelace", "authorId": "acc-1", "created": "2026-10-29T12:46:08Z",
              "body": json.dumps({"type": "doc", "content": [{"type": "paragraph", "content": [
                  {"type": "text", "text": "Is <this> right?"}]}]})},
             {"id": "2", "author": "Alan Turing", "created": "2026-10-29T13:00:00Z", "body": None}]
    comment = CommentPin.model_validate({"id": "P", "x": 180, "y": -130, "posts": posts, **pin})
    return from_dump(DumpFile.model_validate({"board": META, "strategy": "clipboard", "elements": [section],
                                              "comments": [comment.model_dump()]}), report=False)


def test_a_comment_is_pinned_to_the_node_it_was_put_on() -> None:
    board = _board(parentIndex=0)
    (c,) = board.comments
    assert c.parent == board.nodes[0].id
    assert _board(parentIndex=5).comments[0].parent is None, "not a drawn node: a free pin"


def test_the_svg_draws_a_pin_with_its_thread() -> None:
    board = _board(parentIndex=0, resolved=True)
    svg = render_svg(board)
    root = ET.fromstring(svg)  # the thread's text is escaped
    (pin,) = [g for g in root.iter("{http://www.w3.org/2000/svg}g") if g.get("class") == "wb-comment"]
    assert (pin.get("data-parent"), pin.get("data-resolved"), pin.get("data-x")) == (board.nodes[0].id, "true", "180")
    title = pin.find("{http://www.w3.org/2000/svg}title").text
    assert title.startswith("Resolved\n\nAda Lovelace, 29 Oct 2026, 12:46 UTC: Is <this> right?")
    assert "Alan Turing, 29 Oct 2026, 13:00 UTC: " in title
    assert ">AL</text>" in svg, "no picture: the author's initials"
    assert 'display="none"' in svg and "Is &lt;this&gt; right?" in svg


def test_a_canvas_gets_a_card_per_thread() -> None:
    (card,) = [n for n in render_canvas(_board()).nodes if n.id.startswith("comment-")]
    assert (card.type, card.color, card.x, card.y) == ("text", "6", 200, -146)
    assert card.text.startswith("💬 **Ada Lovelace** · 29 Oct 2026, 12:46 UTC\nIs <this> right?")
    (resolved,) = [n for n in render_canvas(_board(resolved=True)).nodes if n.id.startswith("comment-")]
    assert resolved.color == "4" and "✅ **Resolved**" in resolved.text
