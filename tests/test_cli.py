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
    assert "kind name" in result.output


class FakeExtractor:
    """Stands in for the browser session; records how it was used."""

    instances: list[FakeExtractor] = []
    fail_with: dict[str, Exception] = {}

    def __init__(self, base_url: str, state_path: Path, **options: object) -> None:
        self.entered = 0
        self.extracted: list[str] = []
        FakeExtractor.instances.append(self)

    async def __aenter__(self) -> FakeExtractor:
        self.entered += 1
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def extract(self, board, out_dir: Path):
        from wb2canvas.model import DumpFile

        self.extracted.append(board.boardId)
        if board.boardId in self.fail_with:
            raise self.fail_with[board.boardId]
        return DumpFile(board=board, strategy="clipboard")


@pytest.fixture
def fake_extractor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> type[FakeExtractor]:
    import wb2canvas.extract as extract_mod
    import wb2canvas.storage as storage_mod

    FakeExtractor.instances = []
    FakeExtractor.fail_with = {}
    state = tmp_path / "state.json"
    state.write_text("{}")
    monkeypatch.setattr(storage_mod, "storage_state_path", lambda: state)
    monkeypatch.setattr(extract_mod, "Extractor", FakeExtractor)
    return FakeExtractor


def _space_index(tmp_path: Path, *board_ids: str) -> None:
    idx = tmp_path / "out" / "TEST" / "_boards.json"
    idx.parent.mkdir(parents=True, exist_ok=True)
    idx.write_text(json.dumps([{"boardId": b, "title": f"Board {b}", "spaceKey": "TEST"} for b in board_ids]))


def _extract_space(tmp_path: Path):
    return CliRunner().invoke(
        main, ["--out", str(tmp_path / "out"), "--base-url", "https://x.atlassian.net", "extract", "--space", "TEST"],
    )


def test_extract_uses_one_browser_for_the_whole_space(tmp_path: Path, fake_extractor) -> None:
    _space_index(tmp_path, "1", "2", "3")
    result = _extract_space(tmp_path)
    assert result.exit_code == 0, result.output
    (session,) = fake_extractor.instances
    assert session.entered == 1
    assert session.extracted == ["1", "2", "3"]


def test_extract_starts_no_browser_when_everything_is_already_extracted(tmp_path: Path, fake_extractor) -> None:
    _space_index(tmp_path, "1")
    done = tmp_path / "out" / "TEST" / "1" / "dump.json"
    done.parent.mkdir(parents=True)
    done.write_text("{}")
    result = _extract_space(tmp_path)
    assert result.exit_code == 0, result.output
    assert fake_extractor.instances == []
    assert "skip" in result.output


def test_extract_exits_nonzero_but_continues_when_a_board_fails(tmp_path: Path, fake_extractor) -> None:
    _space_index(tmp_path, "1", "2", "3")
    fake_extractor.fail_with = {"2": RuntimeError("simulated extraction failure")}
    result = _extract_space(tmp_path)
    assert result.exit_code == 1
    assert fake_extractor.instances[0].extracted == ["1", "2", "3"], "a bad board does not stop the rest"
    assert "1 of 3 board(s) failed: 2" in result.output


def test_expired_session_stops_the_run(tmp_path: Path, fake_extractor) -> None:
    from wb2canvas.extract import SessionExpiredError

    _space_index(tmp_path, "1", "2", "3")
    fake_extractor.fail_with = {"1": SessionExpiredError("run `wb2canvas auth attach`")}
    result = _extract_space(tmp_path)
    assert result.exit_code == 1
    assert fake_extractor.instances[0].extracted == ["1"], "no point retrying with a dead session"
    assert "3 of 3 board(s) failed" in result.output
    assert "auth attach" in result.output
