"""Icon, sticker and stamp artwork read from the editor per board (drawings.py, the
extractor's read_artwork) and drawn by the renderers; the repo carries none
of it."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import pytest

from confluence_whiteboard_exporter.board import from_dump
from confluence_whiteboard_exporter.canvas import render_canvas
from confluence_whiteboard_exporter.drawings import icon_entry, kind_entry
from confluence_whiteboard_exporter.extract import read_artwork
from confluence_whiteboard_exporter.model import ClipboardElement, DumpFile
from confluence_whiteboard_exporter.shapes import drawing
from confluence_whiteboard_exporter.svg import render_svg

A, B, C = [0, 0, 0, 0], [1, 0, 0, 0], [1, 1, 0, 0]
TRIANGLE = [["L", A, B], ["L", B, C], ["L", C, A]]
# A key-like icon shape, as the editor's shape registry hands it over.
KEY = {"key": "key", "cat": "advanced", "renderer": "graphics", "exterior": "+y", "fit": [0, 0, 200, 200],
       "natural": [160, 160], "fills": [{"color": None, "rule": None, "segs": TRIANGLE}],
       "strokes": [{"color": None, "rule": None, "segs": TRIANGLE}]}
# A library icon: its own colours, even-odd filled.
BUCKET = {"natural": [64, 64], "fills": [{"color": {"kind": "Solid", "rgba": [255, 136, 0, 255]},
                                          "rule": "evenodd", "segs": TRIANGLE}], "strokes": []}
S3 = {"type": "advanced-icon", "position": {"x": 0, "y": 0}, "size": {"x": 160, "y": 160},
      "basisSize": {"x": 108, "y": 1}, "iconId": "Amazon-Simple-Storage-Service", "category": "storage",
      "collection": "aws"}


def _board(*elements: dict, **dump: object):
    return from_dump(DumpFile.model_validate({
        "board": {"boardId": "1", "title": "t", "spaceKey": "S"}, "strategy": "clipboard",
        "elements": [ClipboardElement.model_validate(e) for e in elements], **dump}))


def test_editor_drawings_convert_with_their_colours() -> None:
    key = kind_entry(KEY)
    assert (key["key"], key["aspect"], key["exterior_text"]) == ("key", 1.0, "+y")
    assert "icon" not in key and key["fills"][0]["path"][0] == ["M", A]
    bucket = icon_entry(BUCKET)
    assert bucket["aspect"] == 1.0
    assert bucket["fills"][0] == {"path": [["M", A], ["L", B], ["L", C], ["L", A], ["Z"]],
                                  "colour": "#FF8800", "rule": "evenodd"}


def test_an_icon_shape_is_a_placeholder_until_its_drawing_is_read() -> None:
    assert drawing(33, 0, 0, 100, 100).placeholder
    drawn = drawing(33, 0, 0, 100, 100, extra={33: kind_entry(KEY)})
    assert not drawn.placeholder and {s.colour for s in drawn.sections} == {"fill", "stroke"}


def test_icon_shapes_with_their_drawing_are_drawn_and_not_reported(caplog) -> None:
    caplog.set_level(logging.WARNING)
    key = {"type": "shape", "shape": 33, "position": {"x": 0, "y": 0}, "size": {"x": 100, "y": 100}}
    svg = render_svg(_board(key, drawings={"33": kind_entry(KEY)}))
    assert 'stroke="#292A2E" stroke-width="3"' in svg and "#F7F8F9" not in svg
    assert not [r for r in caplog.records if "placeholders" in r.getMessage()]


def test_a_library_icon_with_its_svg_is_drawn_from_it() -> None:
    href = "media/icon-aws-storage-Amazon-Simple-Storage-Service.svg"
    board = _board(S3, icons={"aws/storage/Amazon-Simple-Storage-Service": href})
    assert re.search(rf'<image [^>]*href="{href}"', render_svg(board))
    (node,) = render_canvas(board).nodes
    assert (node.type, node.file) == ("file", href)


def test_a_library_icon_without_its_svg_is_a_reported_placeholder(caplog) -> None:
    caplog.set_level(logging.WARNING)
    svg = render_svg(_board(S3))
    assert "<image" not in svg and "Amazon Simple Storage Service" in svg
    assert any("1 library icon(s) drawn as placeholders" in r.getMessage() for r in caplog.records)


class _Frame:
    """Answers the graphics probe as the editor would."""

    def __init__(self) -> None:
        self.asked: list = []

    async def evaluate(self, script: str, arg: object = None) -> object:
        if not script.startswith("("):  # the probe itself, injected
            return None
        self.asked.append(arg)
        if "shapeDrawings" in script:
            return {str(k): KEY for k in arg}
        if "libraryIcons" in script:
            return {f"{i['collection']}/{i['category']}/{i['iconId']}": "<svg/>" for i in arg}
        if "stickerImages" in script:
            return {"AWS-Lambda": "/whiteboards/assets/AWS-Lambda.abc.webp"}
        if "download" in script:
            return {k: "UklGRg==" for k in arg}  # "RIFF"
        return None


def test_the_extractor_reads_only_the_artwork_a_board_uses(tmp_path: Path) -> None:
    elements = [ClipboardElement.model_validate(e) for e in (
        {"type": "shape", "shape": 33}, {"type": "shape", "shape": 1}, S3)]
    frame = _Frame()
    art = asyncio.run(read_artwork(frame, elements, None, tmp_path / "media"))
    drawings, icons = art.drawings, art.icons
    assert frame.asked[0] == [33], "the icon kind only, not the rectangle"
    assert list(drawings) == ["33"] and drawings["33"]["key"] == "key"
    href = icons["aws/storage/Amazon-Simple-Storage-Service"]
    assert (tmp_path / href).read_text() == "<svg/>"


def test_without_media_library_icons_are_not_read(tmp_path: Path) -> None:
    frame = _Frame()
    art = asyncio.run(read_artwork(frame, [ClipboardElement.model_validate(S3)], None, None))
    assert (art.drawings, art.icons, art.stickers, art.stamps, frame.asked) == ({}, {}, {}, {}, [])


# A shape kind newer than shape_data.json, as the editor's registry hands it over.
NEW_KIND = 999
NEW = {"key": "new-shape", "cat": "basic", "renderer": "graphics", "fit": None, "natural": [200, 200],
       "fills": [{"color": None, "rule": None, "segs": TRIANGLE}],
       "strokes": [{"color": None, "rule": None, "segs": TRIANGLE}],
       "text": [[0.1, 0.1, 0, 0], [0.9, 0.9, 0, 0]]}


def test_a_shape_kind_newer_than_the_repo_is_drawn_from_what_the_board_read(caplog) -> None:
    caplog.set_level(logging.WARNING)
    shape = {"type": "shape", "shape": NEW_KIND, "position": {"x": 0, "y": 0}, "size": {"x": 200, "y": 100},
             "text": '{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"New"}]}]}'}
    assert drawing(NEW_KIND, 0, 0, 200, 100).placeholder
    board = _board(shape, drawings={str(NEW_KIND): kind_entry(NEW)})
    svg = render_svg(board)
    assert ">New<" in svg and 'stroke-width="3"' in svg
    assert not [r for r in caplog.records if "placeholders" in r.getMessage()]
    # Its text goes in its own text area (here 80% of the box), outline drawn inside the box.
    drawn = drawing(NEW_KIND, -100, -50, 200, 100, extra=board.drawings)
    assert drawn.text == (-80, -40, 160, 80) and drawn.inset


def test_the_extractor_also_reads_shape_kinds_the_repo_does_not_know(tmp_path: Path) -> None:
    frame = _Frame()
    elements = [ClipboardElement.model_validate({"type": "shape", "shape": NEW_KIND})]
    asyncio.run(read_artwork(frame, elements, None, tmp_path / "media"))
    assert frame.asked[0] == [NEW_KIND]


# ------------------------------------------------------------------ stickers

LAMBDA = {"type": "sticker", "position": {"x": 0, "y": 0}, "size": {"x": 120, "y": 120},
          "spriteId": "AWS-Lambda", "rotation": 0.3}


def test_a_sticker_is_its_image_turned_about_its_centre() -> None:
    board = _board(LAMBDA, stickers={"AWS-Lambda": "media/sticker-AWS-Lambda.webp"})
    (node,) = board.nodes
    assert (node.sprite, node.rotation, node.x, node.w) == ("AWS-Lambda", 0.3, -60, 120)
    svg = render_svg(board)
    assert re.search(r'<image x="-60" y="-60" width="120" height="120" href="media/sticker-AWS-Lambda.webp" '
                     r'preserveAspectRatio="xMidYMid meet" transform="rotate\(17.2 0 0\)"/>', svg)
    (card,) = render_canvas(board).nodes
    assert (card.type, card.file) == ("file", "media/sticker-AWS-Lambda.webp")


def test_a_sticker_without_its_image_is_a_reported_placeholder(caplog) -> None:
    caplog.set_level(logging.WARNING)
    svg = render_svg(_board(LAMBDA))
    assert "<image" not in svg and ">AWS-Lambda<" in svg
    assert any("1 sticker(s) drawn as placeholders" in r.getMessage() for r in caplog.records)


def test_fiber_dump_reads_stickers() -> None:
    fiber = {"board": {"K": {"t": "sticker", "si": "ai_platform"}},
             "dimensions": [{"key": "p#K", "val": [10, 20]}, {"key": "s#K", "val": [120, 120]},
                            {"key": "r#K", "val": 0.3}], "zindex": ["K"]}
    board = from_dump(DumpFile.model_validate({"board": {"boardId": "1", "title": "t", "spaceKey": "S"},
                                               "strategy": "fiber", "fiber_dump": fiber}))
    (node,) = board.nodes
    assert (node.sprite, node.rotation, node.x, node.y, node.w) == ("ai_platform", 0.3, -50, -40, 120)


def test_the_extractor_saves_a_boards_sticker_images(tmp_path: Path) -> None:
    frame = _Frame()
    stickers = asyncio.run(read_artwork(frame, [ClipboardElement.model_validate(LAMBDA)], None,
                                        tmp_path / "media")).stickers
    assert stickers == {"AWS-Lambda": "media/sticker-AWS-Lambda.webp"}
    assert (tmp_path / "media" / "sticker-AWS-Lambda.webp").read_bytes() == b"RIFF"


def test_a_turned_box_compares_by_the_upright_box_round_it() -> None:
    from confluence_whiteboard_exporter.compare import _bounds

    # As the editor reported it for a 160 square turned 0.6.
    assert _bounds(0, 0, 160, 160, 0.6) == pytest.approx((-31.2, -31.2, 222.4, 222.4), abs=0.1)
    assert _bounds(0, 0, 160, 100, 0.0) == (0, 0, 160, 100)


# -------------------------------------------------------------------- stamps

STICKY = {"type": "sticky", "position": {"x": 0, "y": 0}, "size": {"x": 144, "y": 144}}
# Put on the sticky: its centre is the sticky's top-left plus the offset.
THUMBS = {"type": "stamp", "position": {"x": 29.36, "y": -31.38}, "size": {"x": 60, "y": 60}, "rotation": 0.0,
          "spriteId": "thumbs-up", "attachedTo": {"parentIndex": 0, "offset": [101.36, 40.62]}}


def test_a_stamp_is_its_svg_on_the_element_it_was_put_on() -> None:
    board = _board(STICKY, THUMBS, stamps={"thumbs-up": "media/stamp-thumbs-up.png"})
    sticky, stamp = board.nodes
    assert (stamp.kind.value, stamp.sprite, stamp.parent) == ("stamp", "thumbs-up", sticky.id)
    assert (stamp.x, stamp.y, stamp.w) == pytest.approx((-0.64, -61.38, 60))
    svg = render_svg(board)
    assert f'data-parent="{sticky.id}"' in svg and 'href="media/stamp-thumbs-up.png"' in svg
    card = render_canvas(board).nodes[-1]
    assert (card.type, card.file) == ("file", "media/stamp-thumbs-up.png")


def test_a_stamp_without_its_svg_is_a_reported_placeholder(caplog) -> None:
    caplog.set_level(logging.WARNING)
    svg = render_svg(_board(STICKY, THUMBS))
    assert "<image" not in svg and ">thumbs-up<" in svg
    assert any("1 stamp(s) drawn as placeholders" in r.getMessage() for r in caplog.records)


def test_fiber_dump_reads_stamps_and_what_they_are_on() -> None:
    fiber = {"board": {"N": {"t": "sticky", "c": [67, 127, 0, 0, 67, 94, 0, 0, 67, 56, 0, 0]},
                       "M": {"t": "stamp", "si": "fire", "at": {"offset": {"0": 101, "1": 40}, "parentId": "N"}}},
             "dimensions": [{"key": "p#N", "val": [0, 0]}, {"key": "s#N", "val": [144, 144]},
                            {"key": "p#M", "val": [29, -32]}, {"key": "s#M", "val": [60, 60]},
                            {"key": "r#M", "val": 0}], "zindex": ["N", "M"]}
    board = from_dump(DumpFile.model_validate({"board": {"boardId": "1", "title": "t", "spaceKey": "S"},
                                               "strategy": "fiber", "fiber_dump": fiber}))
    stamp = next(n for n in board.nodes if n.sprite == "fire")
    assert (stamp.kind.value, stamp.parent, stamp.x, stamp.y) == ("stamp", "N", -1, -62)


def test_the_extractor_saves_a_boards_stamps(tmp_path: Path) -> None:
    class Frame(_Frame):
        async def evaluate(self, script: str, arg: object = None) -> object:
            if script.startswith("(") and "stampImages" in script:
                self.asked.append(arg)
                return {i: "data:image/png;base64,iVBORw==" for i in arg} | {"x": "data:text/html;base64,"}
            return await super().evaluate(script, arg)

    frame = Frame()
    elements = [ClipboardElement.model_validate(e) for e in (STICKY, THUMBS)]
    stamps = asyncio.run(read_artwork(frame, elements, None, tmp_path / "media")).stamps
    assert frame.asked == [["thumbs-up"]]
    assert stamps == {"thumbs-up": "media/stamp-thumbs-up.png"}, "only images are saved"
    assert (tmp_path / "media" / "stamp-thumbs-up.png").read_bytes() == b"\x89PNG"
