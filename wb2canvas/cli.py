from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click
from dotenv import find_dotenv, load_dotenv

if TYPE_CHECKING:
    from .model import DumpFile


def run() -> None:
    """Console-script entry point.

    `.env` is loaded here rather than at import time so importing this module
    (e.g. from tests) never pulls real credentials into the process, and it is
    searched from the current directory rather than from wherever the package
    happens to be installed.
    """
    load_dotenv(find_dotenv(usecwd=True), override=False)
    main()


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


@auth.command("login")
@click.pass_context
def auth_login(ctx: click.Context) -> None:
    """[fallback] Open a Playwright-driven browser to log in."""
    import asyncio as _asyncio

    from .extract import login_interactive
    from .storage import storage_state_path

    state_path = storage_state_path()
    base_url = ctx.obj.get("base_url")
    _asyncio.run(login_interactive(state_path, base_url=base_url))
    click.echo(f"saved {state_path}")


@auth.command("attach")
@click.option("--port", default=9222, show_default=True, help="Chrome remote debug port.")
@click.option(
    "--close-after",
    is_flag=True,
    help="Close the launched Chrome window once the session is saved.",
)
@click.pass_context
def auth_attach(ctx: click.Context, port: int, close_after: bool) -> None:
    """Recommended: launch your real Chrome with debug port, log in, save session."""
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
    help="If set, also convert + deploy each extracted board into this vault.",
)
@click.option(
    "--vault-prefix",
    default="",
    help="Subdirectory within the vault (passed through to convert).",
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
    vault_prefix: str,
) -> None:
    """Extract a board (or every board in a space) via headless browser."""
    import asyncio as _asyncio
    import json as _json

    from .discover import get_whiteboard
    from .extract import extract_board
    from .model import BoardMeta
    from .storage import boards_index_path, dump_path, storage_state_path

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
        boards = [get_whiteboard(base_url, email, token, board_id)]  # type: ignore[arg-type]

    headless = not headed
    failed: list[str] = []
    for b in boards:
        target = dump_path(out_dir, b.spaceKey, b.boardId)
        if target.exists() and not force:
            click.echo(f"skip  {target} (exists; use --force)")
        else:
            click.echo(f"extract {b.boardId} ({b.title!r}) → {target}")
            try:
                dump = _asyncio.run(
                    extract_board(
                        b,
                        base_url,
                        out_dir,
                        state_path,
                        strategy=strategy,  # type: ignore[arg-type]
                        download_media=not no_media,
                        headless=headless,
                    )
                )
            except Exception as e:
                click.echo(f"  FAILED: {e}", err=True)
                failed.append(b.boardId)
                continue
            click.echo(
                f"  wrote {target} (strategy={dump.strategy}, "
                f"elements={len(dump.elements)}, media={len(dump.media)})"
            )

        if vault_dir is not None:
            ctx.invoke(
                convert,
                dump_path=target,
                all_space=None,
                force=force,
                vault_dir=vault_dir,
                vault_prefix=vault_prefix,
            )

    if failed:
        click.echo(f"{len(failed)} of {len(boards)} board(s) failed: {', '.join(failed)}", err=True)
        sys.exit(1)


@main.command()
@click.argument("dump_path", type=click.Path(exists=False, dir_okay=False, path_type=Path), required=False)
@click.option("--all", "all_space", help="Convert every dump in this space.")
@click.option("--force", is_flag=True)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["canvas", "svg"]),
    default="canvas",
    show_default=True,
    help="canvas = JSON Canvas (Obsidian-editable); svg = self-contained SVG (visual fidelity).",
)
@click.option(
    "--vault",
    "vault_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Path to your Obsidian vault. When set, the output + media/ are copied into "
         "<vault>/<vault-prefix>/ and image paths are written relative to the vault root.",
)
@click.option(
    "--vault-prefix",
    default="",
    help="Subdirectory within the vault (e.g. 'Whiteboards/Sample Board'). "
         "Also prepended to image file paths.",
)
@click.option(
    "--collision-fix",
    is_flag=True,
    help="Run an anti-collision pass on the JSON Canvas: nudges overlapping "
         "nodes apart along the axis of least overlap, preserving stacks.",
)
@click.option(
    "--shape-map",
    "shape_map_str",
    default="",
    help="(SVG only) Override stereotype mapping. Format: '4=ellipse,5=diamond,11=note'. "
         "Supported names: rect cylinder ellipse diamond triangle hexagon pentagon "
         "octagon parallelogram trapezoid note document cloud star.",
)
@click.pass_context
def convert(
    ctx: click.Context,
    dump_path: Path | None,
    all_space: str | None,
    force: bool,
    fmt: str,
    vault_dir: Path | None,
    vault_prefix: str,
    collision_fix: bool,
    shape_map_str: str,
) -> None:
    """Convert a dump.json to JSON Canvas (default) or SVG."""
    import shutil as _shutil

    from .model import DumpFile

    if not dump_path and not all_space:
        click.echo("ERROR: provide DUMP_PATH or --all SPACE", err=True)
        sys.exit(2)

    out_dir: Path = ctx.obj["out_dir"]

    if dump_path:
        targets = [dump_path]
    else:
        space_dir = out_dir / all_space  # type: ignore[arg-type]
        if not space_dir.exists():
            click.echo(f"ERROR: no extracted boards under {space_dir}", err=True)
            sys.exit(2)
        targets = sorted(space_dir.glob("*/dump.json"))
        if not targets:
            click.echo(f"ERROR: no dump.json files under {space_dir}", err=True)
            sys.exit(2)

    ext = ".canvas" if fmt == "canvas" else ".svg"
    shape_map_overrides = _parse_shape_map(shape_map_str) if shape_map_str else None

    for target in targets:
        dump = DumpFile.model_validate_json(target.read_text())
        board_id = dump.board.boardId
        prefix_clean = vault_prefix.strip("/")

        if vault_dir is not None:
            dest_root = vault_dir / prefix_clean if prefix_clean else vault_dir
            dest_path = dest_root / f"{board_id}{ext}"
            if dest_path.exists() and not force:
                click.echo(f"skip  {dest_path} (exists; use --force)")
                continue
            dest_root.mkdir(parents=True, exist_ok=True)
            src_media = target.parent / "media"
            copied = 0
            if src_media.exists():
                dest_media = dest_root / "media"
                dest_media.mkdir(parents=True, exist_ok=True)
                for f in src_media.iterdir():
                    if f.is_file():
                        _shutil.copy2(f, dest_media / f.name)
                        copied += 1
            stats = _write_output(fmt, dump, target, dest_path, prefix_clean, collision_fix, shape_map_overrides)
            click.echo(f"deployed → {dest_path}  ({stats}, {copied} media files)")
        else:
            out_path = target.with_name(f"{board_id}{ext}")
            if out_path.exists() and not force:
                click.echo(f"skip  {out_path} (exists; use --force)")
                continue
            stats = _write_output(fmt, dump, target, out_path, prefix_clean, collision_fix, shape_map_overrides)
            click.echo(f"wrote {out_path}  ({stats})")


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
    help="Deploy converted canvases + media into this Obsidian vault.",
)
@click.option("--vault-prefix", default="", help="Subdirectory within the vault.")
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
) -> None:
    """End-to-end: discover → extract → convert every whiteboard in a space."""
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
        )
    except SystemExit as e:
        exit_code = int(e.code or 0)
    if vault_dir is None:
        ctx.invoke(convert, dump_path=None, all_space=space, force=force)
    if exit_code:
        sys.exit(exit_code)


def _write_output(
    fmt: str,
    dump: DumpFile,
    dump_path: Path,
    out_path: Path,
    vault_prefix: str,
    collision_fix: bool,
    shape_map: dict[int, str] | None,
) -> str:
    from .convert import convert_file
    from .storage import atomic_write_text
    from .svg import dump_to_svg

    if fmt == "canvas":
        _, doc = convert_file(dump_path, out_path, vault_prefix=vault_prefix, resolve_collisions=collision_fix)
        return f"{len(doc.nodes)} nodes, {len(doc.edges)} edges"
    if dump.strategy != "clipboard":
        raise click.ClickException(
            f"{dump_path}: SVG needs a clipboard-strategy dump (this one is {dump.strategy!r}); "
            "re-extract with --strategy clipboard --force"
        )
    # SVG resolves image hrefs relative to the SVG file, so they stay `media/...`
    # regardless of vault prefix (only JSON Canvas needs vault-root paths).
    svg = dump_to_svg(dump, shape_map=shape_map)
    atomic_write_text(out_path, svg)
    return f"{len(svg) // 1024} KB"


def _parse_shape_map(s: str) -> dict[int, str]:
    """Parse '4=ellipse,5=diamond' into {4: 'ellipse', 5: 'diamond'}."""
    from .svg import STEREOTYPES

    known = set(STEREOTYPES)
    out: dict[int, str] = {}
    for entry in s.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise click.BadParameter(f"--shape-map entry {entry!r} missing '=' (e.g. '4=ellipse')")
        k, _, v = entry.partition("=")
        try:
            kind = int(k.strip())
        except ValueError as e:
            raise click.BadParameter(f"--shape-map key {k!r} is not an integer") from e
        name = v.strip()
        if name not in known:
            raise click.BadParameter(
                f"--shape-map name {name!r} is unknown; choose from: {', '.join(sorted(known))}"
            )
        out[kind] = name
    return out


if __name__ == "__main__":
    run()
