from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from wb2canvas.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "sample_dump.json"
MEDIA_ID = "3326b2e5-07e9-49ce-9b79-36a2d5a986fe"


@pytest.fixture
def extracted(tmp_path: Path) -> Path:
    """An out/ tree as `extract` would leave it: dump.json plus its media file."""
    board = tmp_path / "out" / "TEST" / "1000001"
    (board / "media").mkdir(parents=True)
    shutil.copy(FIXTURE, board / "dump.json")
    (board / "media" / f"{MEDIA_ID}.jpeg").write_bytes(b"\xff\xd8fake-jpeg")
    return board / "dump.json"


def _deploy(tmp_path: Path, dump: Path, fmt: str) -> Path:
    vault = tmp_path / "vault"
    result = CliRunner().invoke(
        main,
        ["--out", str(tmp_path / "out"), "convert", str(dump), "--format", fmt,
         "--vault", str(vault), "--vault-prefix", "Whiteboards/Sample Board"],
    )
    assert result.exit_code == 0, result.output
    return vault


def test_vault_deploy_canvas_file_paths_resolve_from_vault_root(tmp_path: Path, extracted: Path) -> None:
    vault = _deploy(tmp_path, extracted, "canvas")
    canvas = json.loads((vault / "Whiteboards/Sample Board/1000001.canvas").read_text())
    files = [n["file"] for n in canvas["nodes"] if n["type"] == "file"]
    assert files, "fixture has an image node"
    for f in files:
        assert (vault / f).is_file(), f"Obsidian resolves {f!r} from the vault root"


@pytest.mark.parametrize("fmt", ["svg", "html"])
def test_vault_deploy_image_hrefs_resolve_from_the_output_file(tmp_path: Path, extracted: Path, fmt: str) -> None:
    vault = _deploy(tmp_path, extracted, fmt)
    out = vault / f"Whiteboards/Sample Board/1000001.{fmt}"
    hrefs = re.findall(r'<image [^>]*href="([^"]+)"', out.read_text())
    assert hrefs, "fixture has an image"
    for h in hrefs:
        assert (out.parent / h).is_file(), f"{fmt} resolves {h!r} relative to its own file"


def test_several_formats_in_one_run(tmp_path: Path, extracted: Path) -> None:
    result = CliRunner().invoke(
        main, ["--out", str(tmp_path / "out"), "convert", str(extracted), "-f", "canvas", "-f", "svg", "-f", "html"],
    )
    assert result.exit_code == 0, result.output
    assert sorted(p.suffix for p in extracted.parent.glob("1000001.*")) == [".canvas", ".html", ".svg"]


def test_shape_map_rejects_unknown_stereotype(tmp_path: Path, extracted: Path) -> None:
    result = CliRunner().invoke(
        main, ["--out", str(tmp_path / "out"), "convert", str(extracted), "--format", "svg",
               "--shape-map", "4=elipse"],
    )
    assert result.exit_code != 0
    assert "unknown" in result.output


def test_extract_exits_nonzero_when_a_board_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import wb2canvas.discover as discover_mod
    import wb2canvas.extract as extract_mod
    import wb2canvas.storage as storage_mod
    from wb2canvas.model import BoardMeta

    state = tmp_path / "state.json"
    state.write_text("{}")
    monkeypatch.setattr(storage_mod, "storage_state_path", lambda: state)
    monkeypatch.setattr(
        discover_mod, "get_whiteboard",
        lambda *a, **k: BoardMeta(boardId="42", title="t", spaceKey="TEST"),
    )

    async def boom(*a, **k):
        raise RuntimeError("simulated extraction failure")

    monkeypatch.setattr(extract_mod, "extract_board", boom)

    result = CliRunner().invoke(
        main,
        ["--out", str(tmp_path / "out"), "--base-url", "https://x.atlassian.net",
         "--email", "e", "--token", "t", "extract", "42"],
    )
    assert result.exit_code == 1
    assert "1 of 1 board(s) failed: 42" in result.output
