from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from collections.abc import Callable
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
