from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_shape_data", ROOT / "scripts" / "build_shape_data.py")
build_shape_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_shape_data)

A, B, C = [0, 0, 0, 0], [1, 0, 0, 0], [1, 1, 0, 0]


def test_segments_become_path_commands_with_closed_subpaths() -> None:
    segs = [["L", A, B], ["L", B, C], ["L", C, A], ["M", [0, 0, 5, 5]], ["L", [0, 0, 5, 5], [1, 1, -5, -5]]]
    assert build_shape_data._path(segs) == [
        ["M", A], ["L", B], ["L", C], ["L", A], ["Z"],
        ["M", [0, 0, 5, 5]], ["L", [1, 1, -5, -5]],
    ]


def test_build_keeps_reuse_icons_and_fixed_aspect() -> None:
    dump = {
        "29": {"key": "decision", "cat": "flowchart", "renderer": "replicate", "replicates": 4,
               "exterior": None, "noText": False, "fit": None},
        "34": {"key": "server", "cat": "advanced", "renderer": "graphics", "replicates": None,
               "exterior": "+y", "noText": False, "fit": [0, 0, 200, 200]},
    }
    kinds = build_shape_data.build(dump)["kinds"]
    assert kinds["29"]["same_as"] == 4
    assert kinds["34"] == {"key": "server", "category": "advanced", "renderer": "graphics",
                           "exterior_text": "+y", "aspect": 1.0, "icon": True}


def test_the_shipped_data_covers_every_kind() -> None:
    kinds = json.loads((ROOT / "confluence_whiteboard_exporter" / "shape_data.json").read_text())["kinds"]
    assert sorted(map(int, kinds)) == list(range(89))
