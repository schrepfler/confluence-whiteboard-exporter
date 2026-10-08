"""Snapshots of the reference board: what the editor draws for each cell of
the spec, read from a tester's board in Confluence (see docs/plan.md).

A snapshot is the editor's drawn geometry per cell, as matched back to the
spec, and an image of each cell at 100%. Saved, it is the references in
tests/reference/golden; read again later, it shows whether the editor
still draws the board the same way (`pytest --live`).
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path

from .compare import editor_points, hausdorff
from .reference import Cell, Json, cell_slug, match_board, payload, spec_hash

# How far the editor's drawing may move before it counts as drift. Boards
# built at different paste offsets agree to within 0.05 units (the offset
# is matched to a tenth), and two captures of one board to a colour level.
DRIFT_BOX = 0.5  # board units, any edge
DRIFT_PATH = 0.5  # board units, anywhere along a path
DRIFT_PIXELS = 0.01  # share of a cell's drawn pixels


class SnapshotError(Exception):
    """The reference board cannot be read; the message says what to do."""


@dataclass
class Snapshot:
    geometry: Json  # as geometry.json: spec_hash, cells, editor_bundle
    images: dict[str, bytes] = field(default_factory=dict)  # PNG per cell name

    @property
    def missing(self) -> list[str]:
        """Spec elements not found on the board, as `cell#place`."""
        return [f"{name}#{e['place']}" for name, entries in self.geometry["cells"].items()
                for e in entries if e.get("missing")]


def board_state(cells: list[Cell]) -> Json:
    """Which board is the tester's reference board, checked against the spec."""
    from .storage import reference_state_path

    path = reference_state_path()
    if not path.exists():
        raise SnapshotError("no reference board yet; run `confluence-whiteboard-exporter reference create "
                            "--space KEY` first")
    state = json.loads(path.read_text())
    if state.get("spec_hash") != spec_hash(payload(cells)):
        raise SnapshotError("the spec changed since the board was built; run `confluence-whiteboard-exporter "
                            "reference create` again")
    return state


def take(cells: list[Cell], state: Json) -> Snapshot:
    """Read the board in the browser: its geometry and an image of each
    cell. Also caches the editor's font for the report."""
    import asyncio

    from .extract import SessionExpiredError
    from .storage import storage_state_path

    session = storage_state_path()
    if not session.exists():
        raise SnapshotError(f"no saved browser session at {session}; run `confluence-whiteboard-exporter "
                            "auth attach` first")
    try:
        board, centres, geometry, bundle, images = asyncio.run(
            _read(state["base_url"], state["space"], state["board_id"], session, cells))
    except SessionExpiredError as e:
        raise SnapshotError(str(e)) from e
    golden = match_board(cells, board, centres, geometry)
    golden["editor_bundle"] = bundle
    return Snapshot(golden, images)


async def _read(base_url: str, space: str, board_id: str, session: Path, cells: list[Cell]) -> tuple:
    from .editor import (
        CAPTURE_VIEWPORT,
        browser_context,
        capture_cells,
        editor_bundle,
        fetch_editor_font,
        open_board,
        read_geometry,
        read_stored,
    )
    from .reference import cell_rects, paste_offset
    from .storage import editor_font_path

    async with browser_context(session, viewport=CAPTURE_VIEWPORT) as browser:
        page, frame = await open_board(browser, base_url, space, board_id)
        board, centres = await read_stored(frame)
        geometry, bundle = await read_geometry(frame), await editor_bundle(frame)
        ox, oy = paste_offset(payload(cells), board, centres)
        rects = {name: (x + ox, y + oy, w, h) for name, (x, y, w, h) in cell_rects(cells).items()}
        images = await capture_cells(page, frame, rects)
        await fetch_editor_font(page, frame, editor_font_path())
        await _cache_artwork(frame, cells)
        return board, centres, geometry, bundle, images


async def _cache_artwork(frame, cells: list[Cell]) -> None:  # noqa: ANN001 - a Playwright frame
    """Keep every icon shape's drawing and the spec's library icons, as
    the editor has them, for the report (outside the repo)."""
    from .drawings import kind_entry
    from .extract import GRAPHICS_PROBE
    from .shapes import KIND_NAMES, is_icon
    from .storage import artwork_cache_path, atomic_write_text

    icons = [{"collection": e["collection"], "category": e["category"], "iconId": e["iconId"]}
             for c in cells for e in c.elements if e["type"] == "advanced-icon"]
    await frame.evaluate(GRAPHICS_PROBE)
    kinds = await frame.evaluate("(k) => globalThis.__whiteboardExporterGraphics.shapeDrawings(k)",
                                 [k for k in KIND_NAMES if is_icon(k)])
    svgs = await frame.evaluate("(i) => globalThis.__whiteboardExporterGraphics.libraryIcons(i)", icons)
    atomic_write_text(artwork_cache_path(), json.dumps(
        {"drawings": {k: kind_entry(rec) for k, rec in kinds.items()}, "icons": svgs}))


def write(snapshot: Snapshot, directory: Path, keep: frozenset[str] = frozenset()) -> None:
    """Save as references: geometry.json and images/<cell>.png. Cells in
    `keep` keep the image already there, if any; images of cells not in
    the snapshot go."""
    from .storage import atomic_write_text

    atomic_write_text(directory / "geometry.json", json.dumps(snapshot.geometry, indent=1) + "\n")
    image_dir = directory / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    files = {f"{cell_slug(name)}.png": (name, png) for name, png in snapshot.images.items()}
    for old in image_dir.glob("*.png"):
        if old.name not in files:
            old.unlink()
    for file, (name, png) in files.items():
        if name not in keep or not (image_dir / file).exists():
            (image_dir / file).write_bytes(png)


def refresh(directory: Path, cells: list[Cell], live: Snapshot) -> dict[str, list[str]] | None:
    """Make `live` the references in `directory` and return what changed
    from the ones there, per cell (None if there were none). Cells are
    compared by name, so a spec that gained cells still compares the rest;
    the image of a cell that has not drifted is left alone, so capture
    noise does not rewrite it."""
    before = load(directory, cells) if (directory / "geometry.json").exists() else None
    if before is None:
        write(live, directory)
        return None
    known = [c.name for c in cells if c.name in before.geometry["cells"]]
    changed = drift(before, live, known)
    changed |= {c.name: ["new"] for c in cells if c.name not in before.geometry["cells"]}
    write(live, directory, keep=frozenset(n for n in known if n not in changed))
    return changed


def load(directory: Path, cells: list[Cell]) -> Snapshot:
    """The references saved in `directory`."""
    geometry = json.loads((directory / "geometry.json").read_text())
    images = {c.name: p.read_bytes() for c in cells if (p := directory / "images" / f"{cell_slug(c.name)}.png").exists()}
    return Snapshot(geometry, images)


def drift(reference: Snapshot, live: Snapshot, names: list[str]) -> dict[str, list[str]]:
    """What the editor draws differently now, per cell: an element moved,
    gone or new, a path rerouted, or the cell's image changed."""
    from PIL import Image

    from .report import pixel_difference

    out: dict[str, list[str]] = {}
    for name in names:
        before = {str(e["place"]): e for e in reference.geometry["cells"].get(name, [])}
        now = {str(e["place"]): e for e in live.geometry["cells"].get(name, [])}
        found = []
        for place in sorted(before.keys() | now.keys()):
            a, b = before.get(place), now.get(place)
            what = f"{(a or b)['type']} #{place}"
            if a is None or a.get("missing"):
                if b is not None and not b.get("missing"):
                    found.append(f"{what} is new")
                continue
            if b is None or b.get("missing"):
                found.append(f"{what} is no longer drawn")
                continue
            if "box" in a and "box" in b:
                moved = max(abs(p - q) for p, q in zip(a["box"], b["box"], strict=True))
                if moved > DRIFT_BOX:
                    found.append(f"{what} box moved by {moved:.1f}")
            if "path" in a and "path" in b:
                moved = hausdorff(editor_points(a["path"]), editor_points(b["path"]))
                if moved > DRIFT_PATH:
                    found.append(f"{what} path moved by {moved:.1f}")
        if (png_a := reference.images.get(name)) and (png_b := live.images.get(name)):
            score, _ = pixel_difference(Image.open(io.BytesIO(png_a)), Image.open(io.BytesIO(png_b)))
            if score > DRIFT_PIXELS:
                found.append(f"image: {score:.0%} of drawn pixels differ")
        elif png_a or png_b:
            found.append("image: only one of the two has one")
        if found:
            out[name] = found
    return out
