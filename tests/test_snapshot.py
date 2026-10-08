"""Reading back and comparing snapshots of the reference board
(confluence_whiteboard_exporter/snapshot.py), offline."""

from __future__ import annotations

import copy
import io
from pathlib import Path

import pytest

pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from confluence_whiteboard_exporter.reference import Cell  # noqa: E402
from confluence_whiteboard_exporter.snapshot import Snapshot, drift, load, refresh, write  # noqa: E402


def _png(x: int) -> bytes:
    im = Image.new("RGB", (120, 80), "white")
    ImageDraw.Draw(im).rectangle((x, 20, x + 60, 60), outline=(41, 42, 46), width=3)
    buffer = io.BytesIO()
    im.save(buffer, format="PNG")
    return buffer.getvalue()


def _snapshot() -> Snapshot:
    return Snapshot({"spec_hash": "s", "editor_bundle": "b", "cells": {"cell/a": [
        {"place": 0, "type": "shape", "box": [0.0, 0.0, 100.0, 60.0]},
        {"place": 1, "type": "connector", "box": [100.0, 30.0, 300.0, 31.0],
         "path": {"start": [100.0, 30.0], "end": [300.0, 30.0], "segments": [{"type": "line", "angle": 0.0, "length": 200.0}]}},
        {"place": "caption", "type": "text", "box": [-10.0, -40.0, 50.0, -2.0]},
    ]}}, {"cell/a": _png(20)})


def test_the_same_drawing_has_not_drifted() -> None:
    assert drift(_snapshot(), _snapshot(), ["cell/a"]) == {}


def test_boards_built_at_another_paste_offset_have_not_drifted() -> None:
    live = _snapshot()
    for e in live.geometry["cells"]["cell/a"]:
        e["box"] = [v + 0.05 for v in e["box"]]
    assert drift(_snapshot(), live, ["cell/a"]) == {}


def test_a_moved_box_a_rerouted_path_and_a_changed_image_are_drift() -> None:
    live = _snapshot()
    shape, edge, _ = live.geometry["cells"]["cell/a"]
    shape["box"][3] = 82.0
    edge["path"]["segments"] = [{"type": "line", "angle": 0.1, "length": 200.0}]
    live.images["cell/a"] = _png(30)
    found = drift(_snapshot(), live, ["cell/a"])["cell/a"]
    assert found[0] == "shape #0 box moved by 22.0"
    assert found[1].startswith("connector #1 path moved by")
    assert found[2].startswith("image:")


def test_elements_gone_from_or_new_on_the_board_are_drift() -> None:
    live = _snapshot()
    live.geometry["cells"]["cell/a"][0]["missing"] = True
    live.geometry["cells"]["cell/a"].append({"place": 2, "type": "pathLabel", "box": [0.0, 0.0, 10.0, 10.0]})
    assert drift(_snapshot(), live, ["cell/a"])["cell/a"] == ["shape #0 is no longer drawn", "pathLabel #2 is new"]
    assert live.missing == ["cell/a#0"]


def test_a_snapshot_reads_back_as_written(tmp_path: Path) -> None:
    snap = _snapshot()
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "stale.png").write_bytes(b"old")
    write(snap, tmp_path, [Cell("cell/a")])
    again = load(tmp_path, [Cell("cell/a")])
    assert again.geometry == copy.deepcopy(snap.geometry) and again.images == snap.images
    assert not (tmp_path / "images" / "stale.png").exists(), "images of cells no longer in the spec go"


def test_refreshing_rewrites_only_what_drifted(tmp_path: Path) -> None:
    cells = [Cell("cell/a"), Cell("cell/b")]
    first = _snapshot()
    first.geometry["cells"]["cell/b"] = copy.deepcopy(first.geometry["cells"]["cell/a"])
    first.images["cell/b"] = _png(20)
    assert refresh(tmp_path, cells, first) is None, "nothing to compare with the first time"

    again = copy.deepcopy(first)
    noisy = Image.open(io.BytesIO(_png(20)))
    noisy.putpixel((50, 20), (42, 42, 46))  # a colour level off: capture noise
    buffer = io.BytesIO()
    noisy.save(buffer, format="PNG")
    again.images["cell/a"] = buffer.getvalue()
    again.images["cell/b"] = _png(30)
    changed = refresh(tmp_path, cells, again)
    assert list(changed) == ["cell/b"]
    assert (tmp_path / "images" / "cell-a.png").read_bytes() == first.images["cell/a"], "noise is not written"
    assert (tmp_path / "images" / "cell-b.png").read_bytes() == again.images["cell/b"]


def test_a_spec_that_gained_cells_still_keeps_the_others(tmp_path: Path) -> None:
    first = _snapshot()
    refresh(tmp_path, [Cell("cell/a")], first)
    grown = copy.deepcopy(first)
    grown.geometry["spec_hash"] = "t"
    grown.geometry["cells"]["cell/new"] = copy.deepcopy(first.geometry["cells"]["cell/a"])
    grown.images["cell/new"] = _png(30)
    grown.images["cell/a"] = _png(20)[:-1] + b"\x00"  # other bytes, the same picture: must not be written
    assert refresh(tmp_path, [Cell("cell/a"), Cell("cell/new")], grown) == {"cell/new": ["new"]}
    assert (tmp_path / "images" / "cell-a.png").read_bytes() == first.images["cell/a"]


def test_images_of_cells_showing_artwork_are_kept_outside_the_references(tmp_path: Path) -> None:
    repo, private = tmp_path / "golden", tmp_path / "cache"
    cells = [Cell("cell/a"), Cell("sticker/x", [{"type": "sticker", "spriteId": "x"}])]
    snap = _snapshot()
    snap.geometry["cells"]["sticker/x"] = []
    snap.images["sticker/x"] = _png(30)
    (repo / "images").mkdir(parents=True)
    (repo / "images" / "sticker-x.png").write_bytes(b"committed before")
    write(snap, repo, cells, artwork_dir=private)
    assert sorted(p.name for p in (repo / "images").iterdir()) == ["cell-a.png"], "none of the artwork in the repo"
    assert (private / "sticker-x.png").read_bytes() == snap.images["sticker/x"]
    assert load(repo, cells, artwork_dir=private).images == snap.images
    assert set(load(repo, cells, artwork_dir=tmp_path / "elsewhere").images) == {"cell/a"}, "a clone has none"
