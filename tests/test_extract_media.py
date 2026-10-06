from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from wb2canvas.board import from_dump
from wb2canvas.canvas import render_canvas
from wb2canvas.extract import _capture_media, _keep_only
from wb2canvas.model import DumpFile
from wb2canvas.svg import render_svg

UUID = "06418344-e13f-4c27-acb7-06d59cd24f67"


class FakeResponse:
    def __init__(self, url: str, body: bytes, content_type: str = "image/png", status: int = 200) -> None:
        self.url, self._body, self.status = url, body, status
        self.headers = {"content-type": content_type}

    async def body(self) -> bytes:
        return self._body


def _capture(tmp_path: Path, *responses: FakeResponse) -> tuple[dict[str, str], dict[str, str]]:
    media, kinds = {}, {}
    target = tmp_path / "media"
    target.mkdir(exist_ok=True)
    for r in responses:
        asyncio.run(_capture_media(r, target, media, kinds))
    return media, kinds


def test_the_rendition_the_canvas_loads_is_saved(tmp_path: Path) -> None:
    media, _ = _capture(tmp_path, FakeResponse(f"https://api.media.atlassian.com/file/{UUID}/image?w=64", b"small"))
    assert media == {UUID: f"media/{UUID}.png"}
    assert (tmp_path / "media" / f"{UUID}.png").read_bytes() == b"small"


def test_an_original_replaces_a_rendition_but_not_the_reverse(tmp_path: Path) -> None:
    base = f"https://api.media.atlassian.com/file/{UUID}"
    _capture(tmp_path, FakeResponse(f"{base}/image", b"small"), FakeResponse(f"{base}/binary", b"original"),
             FakeResponse(f"{base}/image", b"small again"))
    assert (tmp_path / "media" / f"{UUID}.png").read_bytes() == b"original"


def test_other_responses_are_ignored(tmp_path: Path) -> None:
    media, _ = _capture(tmp_path, FakeResponse(f"https://example.net/file/{UUID}/image", b"x"),
                        FakeResponse(f"https://api.media.atlassian.com/file/{UUID}/binary", b"x", status=403))
    assert media == {}


def test_files_that_are_not_the_boards_images_are_dropped(tmp_path: Path) -> None:
    other = "cde27fe2-1e32-4232-993f-b3787c8d696f"
    media, _ = _capture(tmp_path, FakeResponse(f"https://api.media.atlassian.com/file/{other}/binary", b"avatar"),
                        FakeResponse(f"https://api.media.atlassian.com/file/{UUID}/image", b"board"))
    _keep_only(media, {UUID}, tmp_path / "media")
    assert media == {UUID: f"media/{UUID}.png"}
    assert sorted(p.name for p in (tmp_path / "media").iterdir()) == [f"{UUID}.png"]


def test_an_image_that_was_not_downloaded_is_a_placeholder(caplog) -> None:
    caplog.set_level(logging.WARNING)
    board = from_dump(DumpFile.model_validate({
        "board": {"boardId": "1", "title": "t", "spaceKey": "S"}, "strategy": "clipboard",
        "elements": [{"type": "image", "fileId": UUID, "position": {"x": 0, "y": 0}, "size": {"x": 64, "y": 48}}],
    }))
    assert "1 image(s) were not downloaded" in caplog.text
    svg = render_svg(board)
    assert "<image" not in svg and ">image</text>" in svg
    (node,) = render_canvas(board).nodes
    assert node.type == "text" and "not downloaded" in node.text


def test_images_showing_the_same_picture_share_its_file(caplog) -> None:
    caplog.set_level(logging.WARNING)
    other = "8bd09b33-0cfa-4c50-8509-e1b050205f4c"
    board = from_dump(DumpFile.model_validate({
        "board": {"boardId": "1", "title": "t", "spaceKey": "S"}, "strategy": "clipboard",
        "media": {UUID: f"media/{UUID}.png"},
        "elements": [
            {"type": "image", "fileId": UUID, "imageHash": "e448", "position": {"x": 0, "y": 0}, "size": {"x": 64, "y": 48}},
            {"type": "image", "fileId": other, "imageHash": "e448", "position": {"x": 99, "y": 0}, "size": {"x": 64, "y": 48}},
        ],
    }))
    assert [n.image.href for n in board.nodes] == [f"media/{UUID}.png"] * 2
    assert "not downloaded" not in caplog.text
