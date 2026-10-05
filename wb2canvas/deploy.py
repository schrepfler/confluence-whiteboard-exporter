"""Write rendered boards to disk, optionally into an Obsidian vault."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .board import Board, load_board
from .canvas import render_canvas
from .html import render_html
from .storage import atomic_write_text
from .svg import render_svg

FORMATS: dict[str, str] = {"canvas": ".canvas", "svg": ".svg", "html": ".html"}


@dataclass(frozen=True)
class Written:
    path: Path
    summary: str
    skipped: bool = False


def render(
    board: Board,
    fmt: str,
    *,
    vault_prefix: str = "",
    collision_fix: bool = False,
    shape_map: dict[int, str] | None = None,
) -> tuple[str, str]:
    """Return (file content, one-line summary) for `board` in `fmt`."""
    if fmt == "canvas":
        doc = render_canvas(board, vault_prefix=vault_prefix, resolve_collisions=collision_fix)
        return doc.to_json(), f"{len(doc.nodes)} nodes, {len(doc.edges)} edges"
    if fmt in ("svg", "html"):
        out = (render_svg if fmt == "svg" else render_html)(board, shape_map=shape_map)
        return out, f"{len(out) // 1024} KB"
    raise ValueError(f"unknown format {fmt!r}; expected one of {', '.join(FORMATS)}")


def export_dump(
    dump_path: Path,
    formats: Iterable[str],
    *,
    dest_dir: Path | None = None,
    vault_prefix: str = "",
    force: bool = False,
    collision_fix: bool = False,
    shape_map: dict[int, str] | None = None,
) -> list[Written]:
    """Render one dump in each format.

    Output goes next to the dump unless `dest_dir` is given (e.g. a vault
    folder), in which case the board's media are copied alongside so
    relative image paths keep resolving. Existing files are kept unless
    `force` is set.
    """
    board = load_board(dump_path)
    dest = dest_dir or dump_path.parent
    results: list[Written] = []
    todo: list[tuple[str, Path]] = []
    for fmt in dict.fromkeys(formats):  # de-duplicate, keep order
        out = dest / f"{board.meta.boardId}{FORMATS[fmt]}"
        if out.exists() and not force:
            results.append(Written(out, "exists; use --force", skipped=True))
        else:
            todo.append((fmt, out))

    copied = 0
    if todo and dest.resolve() != dump_path.parent.resolve():
        copied = copy_media(dump_path.parent / "media", dest / "media")
    for fmt, out in todo:
        content, summary = render(
            board, fmt, vault_prefix=vault_prefix, collision_fix=collision_fix, shape_map=shape_map
        )
        atomic_write_text(out, content)
        if copied:
            summary += f", {copied} media files"
        results.append(Written(out, summary))
    return results


def copy_media(src: Path, dst: Path) -> int:
    if not src.is_dir():
        return 0
    dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    for f in src.iterdir():
        if f.is_file():
            shutil.copy2(f, dst / f.name)
            copied += 1
    return copied
