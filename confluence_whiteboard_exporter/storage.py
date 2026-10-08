from __future__ import annotations

import os
import tempfile
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir

APP = "confluence-whiteboard-exporter"  # names the per-user config and cache directories


def storage_state_path() -> Path:
    return Path(user_config_dir(APP)) / "storage_state.json"


def reference_state_path() -> Path:
    """Which whiteboard is the reference board on which site, outside the repo."""
    return Path(user_config_dir(APP)) / "reference.json"


def editor_font_path() -> Path:
    """The editor's font, cached from the live page for our renders in the
    reference report. Never part of the repo."""
    return Path(user_cache_dir(APP)) / "fonts" / "AtlassianSans-latin.woff2"


def artwork_cache_path() -> Path:
    """The icon artwork read from the editor by `reference snapshot`, for
    the icon cells of the reference report. Never part of the repo."""
    return Path(user_cache_dir(APP)) / "artwork.json"


def chrome_profile_dir() -> Path:
    """Private, persistent profile for `auth attach`. Kept out of the shared
    system temp dir so other local users cannot pre-create or read it."""
    p = Path(user_cache_dir(APP)) / "chrome-profile"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(p, 0o700)
    return p


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


def atomic_write_text(path: Path, text: str, mode: int = 0o644) -> None:
    """Write via a temp file in the same directory, then rename into place.

    A crash mid-write never leaves a truncated file behind — important because
    the extractor skips any board whose dump already exists. The temp file is
    created with `mode` from the start (mkstemp uses 0600), so secrets such as
    session cookies are never briefly world-readable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
