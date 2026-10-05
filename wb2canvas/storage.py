from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir


def storage_state_path() -> Path:
    return Path(user_config_dir("wb2canvas")) / "storage_state.json"


def boards_index_path(out_dir: Path, space_key: str) -> Path:
    return out_dir / space_key / "_boards.json"


def board_dir(out_dir: Path, space_key: str, board_id: str) -> Path:
    return out_dir / space_key / board_id


def dump_path(out_dir: Path, space_key: str, board_id: str) -> Path:
    return board_dir(out_dir, space_key, board_id) / "dump.json"


def canvas_path(out_dir: Path, space_key: str, board_id: str) -> Path:
    return board_dir(out_dir, space_key, board_id) / f"{board_id}.canvas"


def media_dir(out_dir: Path, space_key: str, board_id: str) -> Path:
    return board_dir(out_dir, space_key, board_id) / "media"


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p
