from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import click
from dotenv import find_dotenv, load_dotenv

from .model import BoardMeta
from .shapes import KIND_BY_NAME, KIND_NAMES, can_draw
from .storage import dump_path

if TYPE_CHECKING:
    from .deploy import Written


def run() -> None:
    """Console-script entry point.

    `.env` is loaded here rather than at import time so importing this module
    (e.g. from tests) never pulls real credentials into the process, and it is
    searched from the current directory rather than from wherever the package
    happens to be installed.
    """
    load_dotenv(find_dotenv(usecwd=True), override=False)
    main()


F = TypeVar("F", bound=Callable[..., Any])

FORMAT_HELP = (
    "Output format; repeat for several. canvas = JSON Canvas (editable in "
    "Obsidian), svg = static replica (safe to embed), html = interactive "
    "viewer (drag, pan, zoom)."
)


def format_option(default: tuple[str, ...] = ("canvas",)) -> Callable[[F], F]:
    return click.option(
        "-f", "--format", "formats",
        type=click.Choice(["canvas", "svg", "html"]),
        multiple=True,
        default=default,
        show_default=True,
        help=FORMAT_HELP,
    )


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return v if v else None


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging.")
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("out"),
    show_default=True,
    help="Output root directory.",
)
@click.option("--base-url", default=lambda: _env("CONFLUENCE_BASE_URL"), help="Confluence base URL.")
@click.option("--email", default=lambda: _env("CONFLUENCE_EMAIL"), help="Atlassian account email.")
@click.option("--token", default=lambda: _env("CONFLUENCE_API_TOKEN"), help="Atlassian API token.")
@click.pass_context
def main(ctx: click.Context, verbose: bool, out_dir: Path, base_url: str | None, email: str | None, token: str | None) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="  %(message)s",
        force=True,
    )
    ctx.ensure_object(dict)
    ctx.obj.update(
        verbose=verbose,
        out_dir=out_dir,
        base_url=base_url,
        email=email,
        token=token,
    )


@main.group()
def auth() -> None:
    """Manage browser session for the extractor."""


@auth.command("attach")
@click.option("--port", default=9222, show_default=True, help="Chrome remote debug port.")
@click.option(
    "--close-after",
    is_flag=True,
    help="Close the launched Chrome window once the session is saved.",
)
@click.pass_context
def auth_attach(ctx: click.Context, port: int, close_after: bool) -> None:
    """Log in through your own Chrome and save the session for extraction.

    Launches Google Chrome with a remote-debugging port and a private profile.
    You sign in as a human (SSO bot detection never sees automation); on
    <Enter> the session cookies are captured over CDP.
    """
    import asyncio as _asyncio

    from .extract import login_attach
    from .storage import storage_state_path

    state_path = storage_state_path()
    base_url = ctx.obj.get("base_url")
    _asyncio.run(
        login_attach(
            state_path,
            base_url=base_url,
            port=port,
            keep_chrome=not close_after,
        )
    )


REFERENCE_TITLE = "wb2canvas reference"


@main.group()
def reference() -> None:
    """The reference board the tests compare against (see docs/plan.md)."""


@reference.command("create")
@click.option("--space", required=True, help="Space to put the board in, e.g. your personal space (~<account id>).")
@click.option("--board", "board_id", help="Reuse this whiteboard; its title must start with "
                                          f"'{REFERENCE_TITLE}'. It is cleared first.")
@click.option("--headed", is_flag=True, help="Show the browser.")
@click.pass_context
def reference_create(ctx: click.Context, space: str, board_id: str | None, headed: bool) -> None:
    """Build the reference board: create a whiteboard (or reuse the one
    made before), clear it and paste every cell of the spec into it."""
    import asyncio as _asyncio
    import json as _json
    from datetime import UTC, datetime

    from .discover import create_whiteboard, get_whiteboard
    from .reference import clipboard_html, payload, spec, spec_hash
    from .storage import atomic_write_text, reference_state_path, storage_state_path

    base_url, email, token = ctx.obj["base_url"], ctx.obj["email"], ctx.obj["token"]
    if not (base_url and email and token):
        click.echo("ERROR: set CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN.", err=True)
        sys.exit(2)
    state_path, session = reference_state_path(), storage_state_path()
    if not session.exists():
        click.echo(f"ERROR: no saved browser session at {session}. Run `wb2canvas auth attach` first.", err=True)
        sys.exit(2)
    state = _json.loads(state_path.read_text()) if state_path.exists() else {}
    known = state if (state.get("base_url"), state.get("space")) == (base_url, space) else {}

    board_id = board_id or known.get("board_id")
    if board_id:
        with _rest_errors(base_url, email, token, f"whiteboard {board_id}"):
            meta = get_whiteboard(base_url, email, token, board_id, space_key=space)
        if board_id != known.get("board_id") and not meta.title.startswith(REFERENCE_TITLE):
            click.echo(f"ERROR: whiteboard {board_id} is titled {meta.title!r}; refusing to clear a board "
                       f"that is not a reference board (its title must start with '{REFERENCE_TITLE}').", err=True)
            sys.exit(2)
    else:
        with _rest_errors(base_url, email, token, f"space {space!r}"):
            meta = create_whiteboard(base_url, email, token, space, REFERENCE_TITLE)
        board_id = meta.boardId
        click.echo(f"created whiteboard {board_id} in {space}")

    cells = spec()
    elements = payload(cells)
    _asyncio.run(_build_reference(base_url, space, board_id, clipboard_html(elements), len(elements),
                                  session, headless=not headed))
    atomic_write_text(state_path, _json.dumps({
        "base_url": base_url, "space": space, "board_id": board_id, "spec_hash": spec_hash(elements),
        "elements": len(elements), "built": datetime.now(UTC).isoformat(timespec="seconds"),
    }, indent=2))
    click.echo(f"reference board {board_id}: {len(cells)} cells, {len(elements)} elements")


GOLDEN_DIR = Path("tests/reference/golden")


@reference.command("snapshot")
@click.option("--out", "golden_dir", type=click.Path(file_okay=False, path_type=Path), default=GOLDEN_DIR,
              show_default=True, help="Where to write the references.")
@click.pass_context
def reference_snapshot(ctx: click.Context, golden_dir: Path) -> None:
    """Read what the editor drew on the reference board and save it as the
    references the tests compare against: its geometry per cell, and an
    image of each cell at 100%."""
    import asyncio as _asyncio
    import json as _json

    from .reference import cell_slug, match_board, payload, spec, spec_hash
    from .storage import atomic_write_text, reference_state_path, storage_state_path

    base_url = ctx.obj["base_url"]
    state_path = reference_state_path()
    if not base_url or not state_path.exists():
        click.echo("ERROR: no reference board yet; run `wb2canvas reference create --space KEY` first.", err=True)
        sys.exit(2)
    state = _json.loads(state_path.read_text())
    cells = spec()
    if state.get("spec_hash") != spec_hash(payload(cells)):
        click.echo("ERROR: the spec changed since the board was built; run `wb2canvas reference create` again.",
                   err=True)
        sys.exit(2)

    board, centres, geometry, bundle, images = _asyncio.run(
        _read_reference(base_url, state["space"], state["board_id"], storage_state_path(), cells))
    golden = match_board(cells, board, centres, geometry)
    golden["editor_bundle"] = bundle
    missing = [f"{name}#{e['place']}" for name, entries in golden["cells"].items() for e in entries if e.get("missing")]
    target = golden_dir / "geometry.json"
    atomic_write_text(target, _json.dumps(golden, indent=1) + "\n")
    image_dir = golden_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    for old in image_dir.glob("*.png"):
        old.unlink()
    for name, png in images.items():
        (image_dir / f"{cell_slug(name)}.png").write_bytes(png)
    click.echo(f"wrote {target} and {len(images)} images in {image_dir}: {len(golden['cells'])} cells, editor {bundle}")
    if missing:
        click.echo(f"  {len(missing)} element(s) not found on the board: {', '.join(missing[:10])}", err=True)


@reference.command("report")
@click.option("--out", "target", type=click.Path(dir_okay=False, path_type=Path),
              default=Path("out/report/index.html"), show_default=True)
@click.option("--no-font", is_flag=True, help="Render ours without the editor's font, even if it is cached.")
def reference_report(target: Path, no_font: bool) -> None:
    """Write a page showing each cell of the reference board as the editor
    draws it beside our export of it, with what differs. Needs no
    Confluence: it uses the references in tests/reference/golden."""
    import json as _json

    from .reference import spec
    from .report import build, write
    from .storage import editor_font_path

    golden_path = GOLDEN_DIR / "geometry.json"
    if not golden_path.exists():
        click.echo(f"ERROR: no references at {golden_path}; run `wb2canvas reference snapshot`.", err=True)
        sys.exit(2)
    golden = _json.loads(golden_path.read_text())
    font = None if no_font else editor_font_path()
    if font is not None and not font.exists():
        click.echo("  the editor's font is not cached (run `wb2canvas reference snapshot`); "
                   "our text is set in a fallback font", err=True)
        font = None
    rows = build(spec(), golden, GOLDEN_DIR / "images", font)
    write(rows, target, golden.get("editor_bundle", ""))
    counts = {s: sum(r.status == s for r in rows) for s in ("pass", "warn", "known", "fail")}
    click.echo(f"wrote {target}: {counts['pass']} pass, {counts['warn']} warn, {counts['known']} known, "
               f"{counts['fail']} fail")


METRIC_CHARS = [chr(c) for c in range(33, 127)] + list("\u2019\u2018\u201c\u201d\u2013\u2014\u2022\u2026\u00e9\u00e8\u00e0\u00fc\u00f6\u00e4")


@reference.command("metrics")
@click.option("--out", "target", type=click.Path(dir_okay=False, path_type=Path),
              default=Path(__file__).with_name("text_metrics.json"), show_default=True)
@click.pass_context
def reference_metrics(ctx: click.Context, target: Path) -> None:
    """Measure the editor's text with its own text engine, on the reference
    board: each character's width in each style, and how whole strings
    compare with their characters' widths."""
    import asyncio as _asyncio
    import json as _json
    import statistics

    from .reference import doc, heading, para, spec
    from .storage import atomic_write_text, reference_state_path, storage_state_path

    state_path = reference_state_path()
    if not ctx.obj["base_url"] or not state_path.exists():
        click.echo("ERROR: no reference board yet; run `wb2canvas reference create --space KEY` first.", err=True)
        sys.exit(2)
    state = _json.loads(state_path.read_text())
    styles = {"400": lambda t: doc(para(t)), "600": lambda t: doc(para(t, bold=True)),
              **{f"h{n}": (lambda t, n=n: doc(heading(t, n))) for n in range(1, 7)}}
    repeat = 10
    items, keys = [], []
    for style, make in styles.items():
        for ch in METRIC_CHARS:
            items.append({"adf": make(ch * repeat), "width": 10_000})
            keys.append((style, ch))
        for probe in ("aa", "a a"):  # a space is the difference
            items.append({"adf": make(probe), "width": 10_000})
            keys.append((style, probe))
    captions = [c.name for c in spec()]
    items += [{"adf": doc(para(name)), "width": 10_000} for name in captions]

    results = _asyncio.run(_measure(ctx.obj["base_url"], state["space"], state["board_id"], items,
                                    storage_state_path()))
    table: dict[str, dict[str, float]] = {style: {} for style in styles}
    for (style, key), r in zip(keys, results, strict=False):
        table[style][key] = r["contentWidth"]
    for widths in table.values():
        widths[" "] = widths.pop("a a") - widths.pop("aa")
        for ch in METRIC_CHARS:
            widths[ch] = round(widths[ch] / repeat, 4)
        widths[" "] = round(widths[" "], 4)
    summed = [sum(table["400"].get(ch, 0.0) for ch in name) for name in captions]
    fit = statistics.median(r["contentWidth"] / w for r, w in zip(results[len(keys):], summed, strict=True))
    atomic_write_text(target, _json.dumps({
        "source": "the whiteboard editor's text engine, by `wb2canvas reference metrics`; widths only",
        "fit": round(fit, 5), "styles": table}, ensure_ascii=False, separators=(",", ":")) + "\n")
    click.echo(f"wrote {target}: {len(METRIC_CHARS) + 1} characters in {len(styles)} styles, fit {fit:.4f}")


async def _measure(base_url: str, space: str, board_id: str, items: list[dict], session: Path) -> list[dict]:
    from .editor import browser_context, measure_text, open_board

    async with browser_context(session) as browser:
        _, frame = await open_board(browser, base_url, space, board_id)
        return await measure_text(frame, items)


async def _read_reference(base_url: str, space: str, board_id: str, session: Path, cells: list) -> tuple:
    from .editor import (CAPTURE_VIEWPORT, browser_context, capture_cells, editor_bundle, fetch_editor_font,
                         open_board, read_geometry, read_stored)
    from .reference import cell_rects, paste_offset, payload
    from .storage import editor_font_path

    async with browser_context(session, viewport=CAPTURE_VIEWPORT) as browser:
        page, frame = await open_board(browser, base_url, space, board_id)
        board, centres = await read_stored(frame)
        geometry, bundle = await read_geometry(frame), await editor_bundle(frame)
        ox, oy = paste_offset(payload(cells), board, centres)
        rects = {name: (x + ox, y + oy, w, h) for name, (x, y, w, h) in cell_rects(cells).items()}
        images = await capture_cells(page, frame, rects)
        await fetch_editor_font(page, frame, editor_font_path())
        return board, centres, geometry, bundle, images


async def _build_reference(base_url: str, space: str, board_id: str, html: str, expected: int,
                           session: Path, *, headless: bool) -> None:
    from .editor import browser_context, clear_board, element_count, open_board, paste

    async with browser_context(session, headless=headless) as browser:
        page, frame = await open_board(browser, base_url, space, board_id)
        if await element_count(frame):
            await clear_board(page, frame)
        await paste(page, frame, html, expected)
        await page.close()
        # Read it back from a fresh page: what reached the server.
        page, frame = await open_board(browser, base_url, space, board_id)
        stored = await element_count(frame)
        await page.close()
    if stored != expected:
        raise click.ClickException(f"the board holds {stored} elements after reloading, not {expected}")


@main.command()
@click.argument("space")
@click.option("--method", type=click.Choice(["tree", "cql"]), default="tree", show_default=True)
@click.pass_context
def discover(ctx: click.Context, space: str, method: str) -> None:
    """Enumerate whiteboards in a space."""
    import json as _json

    from .discover import list_whiteboards_in_space
    from .storage import boards_index_path, ensure_dir

    base_url = ctx.obj["base_url"]
    email = ctx.obj["email"]
    token = ctx.obj["token"]
    if not (base_url and email and token):
        click.echo(
            "ERROR: set CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN "
            "(or pass --base-url/--email/--token).",
            err=True,
        )
        sys.exit(2)

    out_dir: Path = ctx.obj["out_dir"]
    with _rest_errors(base_url, email, token, f"space {space!r}"):
        boards = list_whiteboards_in_space(base_url, email, token, space, method=method)
    target = boards_index_path(out_dir, space)
    ensure_dir(target.parent)
    target.write_text(
        _json.dumps([b.model_dump(exclude_none=True) for b in boards], indent=2)
    )
    click.echo(f"wrote {target}  ({len(boards)} whiteboards)")


@main.command()
@click.argument("board_id", required=False)
@click.option("--space", help="Extract every board in this space (reads _boards.json).")
@click.option("--strategy", type=click.Choice(["clipboard", "fiber", "auto"]), default="auto", show_default=True)
@click.option("--force", is_flag=True, help="Re-extract even if dump.json exists.")
@click.option("--no-media", is_flag=True, help="Skip image downloads.")
@click.option("--headed", is_flag=True, help="Run Chromium in headed (visible) mode.")
@click.option(
    "--vault",
    "vault_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="If set, also render each extracted board into this vault.",
)
@format_option()
@click.option(
    "--vault-prefix",
    default="",
    help="Subdirectory within the vault.",
)
@click.pass_context
def extract(
    ctx: click.Context,
    board_id: str | None,
    space: str | None,
    strategy: str,
    force: bool,
    no_media: bool,
    headed: bool,
    vault_dir: Path | None,
    formats: tuple[str, ...],
    vault_prefix: str,
) -> None:
    """Extract a board (or every board in a space) via headless browser."""
    import asyncio as _asyncio
    import json as _json

    from .deploy import export_dump
    from .discover import get_whiteboard
    from .storage import boards_index_path, storage_state_path

    if not board_id and not space:
        click.echo("ERROR: provide BOARD_ID or --space SPACE", err=True)
        sys.exit(2)

    base_url = ctx.obj["base_url"]
    if not base_url:
        click.echo(
            "ERROR: set CONFLUENCE_BASE_URL (or pass --base-url).",
            err=True,
        )
        sys.exit(2)

    state_path = storage_state_path()
    if not state_path.exists():
        click.echo(
            f"ERROR: no saved browser session at {state_path}. "
            "Run `wb2canvas auth attach` first.",
            err=True,
        )
        sys.exit(2)

    out_dir: Path = ctx.obj["out_dir"]

    boards: list[BoardMeta]
    if space:
        idx = boards_index_path(out_dir, space)
        if not idx.exists():
            click.echo(
                f"ERROR: {idx} not found. Run `wb2canvas discover {space}` first.",
                err=True,
            )
            sys.exit(2)
        raw = _json.loads(idx.read_text())
        boards = [BoardMeta.model_validate(b) for b in raw]
    else:
        email = ctx.obj["email"]
        token = ctx.obj["token"]
        if not (email and token):
            click.echo(
                "ERROR: extracting a single board id needs CONFLUENCE_EMAIL/_API_TOKEN "
                "to fetch metadata. Pass --space instead, or set the env vars.",
                err=True,
            )
            sys.exit(2)
        with _rest_errors(base_url, email, token, f"whiteboard {board_id}"):  # type: ignore[arg-type]
            boards = [get_whiteboard(base_url, email, token, board_id)]  # type: ignore[arg-type]

    todo = [b for b in boards if force or not dump_path(out_dir, b.spaceKey, b.boardId).exists()]
    for b in boards:
        if b not in todo:
            click.echo(f"skip  {dump_path(out_dir, b.spaceKey, b.boardId)} (exists; use --force)")
    failed = _asyncio.run(
        _extract_boards(
            todo, base_url, state_path, out_dir,
            strategy=strategy, download_media=not no_media, headless=not headed,
        )
    ) if todo else []

    if vault_dir is not None:
        for b in boards:
            target = dump_path(out_dir, b.spaceKey, b.boardId)
            if b.boardId in failed or not target.exists():
                continue
            _echo_results(
                export_dump(
                    target,
                    formats,
                    dest_dir=_vault_dest(vault_dir, vault_prefix),
                    vault_prefix=vault_prefix.strip("/"),
                    force=force,
                )
            )

    if failed:
        click.echo(f"{len(failed)} of {len(boards)} board(s) failed: {', '.join(failed)}", err=True)
        sys.exit(1)


@main.command()
@click.argument("dump_path", type=click.Path(exists=False, dir_okay=False, path_type=Path), required=False)
@click.option("--all", "all_space", help="Convert every dump in this space.")
@click.option("--force", is_flag=True)
@format_option()
@click.option(
    "--vault",
    "vault_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Obsidian vault to write into: outputs and media/ go to <vault>/<vault-prefix>/. "
         "Canvas image paths are written relative to the vault root, as Obsidian expects.",
)
@click.option(
    "--vault-prefix",
    default="",
    help="Subdirectory within the vault (e.g. 'Whiteboards/Sample Board').",
)
@click.option(
    "--collision-fix",
    is_flag=True,
    help="(canvas) Nudge overlapping nodes apart along the axis of least overlap. "
         "Off by default: some overlaps are deliberate in the source.",
)
@click.option(
    "--shape-map",
    "shape_map_str",
    default="",
    help="(svg/html) Draw shape kinds as other kinds, by number or name, e.g. "
         "'server=database,60=ellipse'. Kinds are listed in docs/confluence-whiteboard-model.md; "
         "icons cannot be targets.",
)
@click.pass_context
def convert(
    ctx: click.Context,
    dump_path: Path | None,
    all_space: str | None,
    force: bool,
    formats: tuple[str, ...],
    vault_dir: Path | None,
    vault_prefix: str,
    collision_fix: bool,
    shape_map_str: str,
) -> None:
    """Render dump.json files as JSON Canvas, SVG and/or an HTML viewer."""
    from .deploy import export_dump

    if not dump_path and not all_space:
        click.echo("ERROR: provide DUMP_PATH or --all SPACE", err=True)
        sys.exit(2)

    out_dir: Path = ctx.obj["out_dir"]
    if dump_path:
        targets = [dump_path]
    else:
        space_dir = out_dir / all_space  # type: ignore[arg-type]
        targets = sorted(space_dir.glob("*/dump.json"))
        if not targets:
            click.echo(f"ERROR: no extracted boards (dump.json) under {space_dir}", err=True)
            sys.exit(2)

    shape_map = _parse_shape_map(shape_map_str) if shape_map_str else None
    for target in targets:
        _echo_results(
            export_dump(
                target,
                formats,
                dest_dir=_vault_dest(vault_dir, vault_prefix),
                vault_prefix=vault_prefix.strip("/"),
                force=force,
                collision_fix=collision_fix,
                shape_map=shape_map,
            )
        )


@main.command()
@click.argument("space")
@click.option("--method", type=click.Choice(["tree", "cql"]), default="tree", show_default=True)
@click.option("--strategy", type=click.Choice(["clipboard", "fiber", "auto"]), default="auto", show_default=True)
@click.option("--force", is_flag=True)
@click.option("--no-media", is_flag=True)
@click.option("--headed", is_flag=True)
@click.option(
    "--vault",
    "vault_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Deploy rendered boards + media into this Obsidian vault.",
)
@click.option("--vault-prefix", default="", help="Subdirectory within the vault.")
@format_option()
@click.pass_context
def export(
    ctx: click.Context,
    space: str,
    method: str,
    strategy: str,
    force: bool,
    no_media: bool,
    headed: bool,
    vault_dir: Path | None,
    vault_prefix: str,
    formats: tuple[str, ...],
) -> None:
    """End-to-end: discover → extract → render every whiteboard in a space."""
    ctx.invoke(discover, space=space, method=method)
    # A failed board must not stop the rest from being converted; remember
    # the failure and report it once everything that could be done is done.
    exit_code = 0
    try:
        ctx.invoke(
            extract,
            board_id=None,
            space=space,
            strategy=strategy,
            force=force,
            no_media=no_media,
            headed=headed,
            vault_dir=vault_dir,
            vault_prefix=vault_prefix,
            formats=formats,
        )
    except SystemExit as e:
        exit_code = int(e.code or 0)
    if vault_dir is None:
        ctx.invoke(convert, dump_path=None, all_space=space, force=force, formats=formats)
    if exit_code:
        sys.exit(exit_code)


async def _extract_boards(
    boards: list[BoardMeta],
    base_url: str,
    state_path: Path,
    out_dir: Path,
    **options: Any,
) -> list[str]:
    """Extract `boards` through one browser; return the ids that failed.

    An expired session fails every remaining board the same way, so the
    run stops at the first one instead of retrying each board.
    """
    from .extract import Extractor, SessionExpiredError

    failed: list[str] = []
    async with Extractor(base_url, state_path, **options) as ex:
        for i, b in enumerate(boards):
            target = dump_path(out_dir, b.spaceKey, b.boardId)
            click.echo(f"extract {b.boardId} ({b.title!r}) → {target}")
            try:
                dump = await ex.extract(b, out_dir)
            except SessionExpiredError as e:
                click.echo(f"  FAILED: {e}", err=True)
                failed.extend(x.boardId for x in boards[i:])
                break
            except Exception as e:  # noqa: BLE001 - one bad board must not stop the rest
                click.echo(f"  FAILED: {e}", err=True)
                failed.append(b.boardId)
                continue
            click.echo(
                f"  wrote {target} (strategy={dump.strategy}, "
                f"elements={len(dump.elements)}, media={len(dump.media)})"
            )
    return failed


@contextmanager
def _rest_errors(base_url: str, email: str, token: str, what: str) -> Iterator[None]:
    """Turn a failed Confluence REST call into one line saying why."""
    import httpx

    from .discover import credentials_rejected

    try:
        yield
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if credentials_rejected(base_url, email, token):
            click.echo(
                f"ERROR: Confluence rejected the API token ({status}); tokens expire. Create one at "
                "https://id.atlassian.com/manage-profile/security/api-tokens and set CONFLUENCE_API_TOKEN.",
                err=True,
            )
        elif status == 404:
            click.echo(f"ERROR: {what} not found, or this account cannot see it ({status}).", err=True)
        else:
            click.echo(f"ERROR: Confluence answered {status} for {e.request.url.path}.", err=True)
        sys.exit(1)
    except httpx.TransportError as e:
        click.echo(f"ERROR: cannot reach Confluence at {base_url}: {e}", err=True)
        sys.exit(1)


def _vault_dest(vault_dir: Path | None, vault_prefix: str) -> Path | None:
    if vault_dir is None:
        return None
    prefix = vault_prefix.strip("/")
    return vault_dir / prefix if prefix else vault_dir


def _echo_results(results: list[Written]) -> None:
    for r in results:
        verb = "skip " if r.skipped else "wrote"
        click.echo(f"{verb} {r.path}  ({r.summary})")


def _parse_shape_map(s: str) -> dict[int, int]:
    """Parse 'server=database,60=ellipse' into {34: 13, 60: 2}."""
    out: dict[int, int] = {}
    for entry in s.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise click.BadParameter(f"--shape-map entry {entry!r} missing '=' (e.g. 'server=database')")
        k, _, v = (part.strip() for part in entry.partition("="))
        kind, target = _shape_kind(k), _shape_kind(v)
        if not can_draw(target):
            raise click.BadParameter(f"--shape-map target {v!r} has no drawing (icons cannot be targets)")
        out[kind] = target
    return out


def _shape_kind(name: str) -> int:
    kind = int(name) if name.isdigit() else KIND_BY_NAME.get(name)
    if kind is None or kind not in KIND_NAMES:
        raise click.BadParameter(f"--shape-map: {name!r} is neither a shape kind number nor a kind name")
    return kind


if __name__ == "__main__":
    run()
