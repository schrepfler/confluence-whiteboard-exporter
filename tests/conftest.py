"""`--live` and `--update-goldens`: check the tester's reference board in
Confluence against the references, or rewrite the references from it (see
tests/test_reference_live.py and docs/plan.md).

Both need the board made by `confluence-whiteboard-exporter reference
create` and the browser session saved by `auth attach`; the board's site
comes from the reference state that `create` recorded, so `.env` is not
read. Without them every test is offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).parent / "reference" / "golden"
_LIVE = pytest.StashKey[dict]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("reference board")
    group.addoption("--live", action="store_true",
                    help="also read your reference board in Confluence and report where the editor now draws it "
                         "differently from tests/reference/golden")
    group.addoption("--update-goldens", action="store_true",
                    help="read your reference board and rewrite tests/reference/golden from it, before the tests "
                         "compare against them (implies --live)")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: reads the tester's reference board in Confluence (--live)")


def _live(config: pytest.Config) -> bool:
    return bool(config.getoption("--live") or config.getoption("--update-goldens"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Without --live the live tests are left out (reported as deselected)."""
    if _live(config):
        return
    live = [item for item in items if "live" in item.keywords]
    if live:
        config.hook.pytest_deselected(items=live)
        items[:] = [item for item in items if "live" not in item.keywords]


@pytest.fixture(scope="session", autouse=True)
def live_snapshot(request: pytest.FixtureRequest):  # noqa: ANN201 - a Snapshot, or None offline
    """The board as the editor draws it now, read once per run. With
    --update-goldens it becomes the references before any test reads them,
    and what changed is listed at the end of the run."""
    config = request.config
    if not _live(config):
        return None
    from confluence_whiteboard_exporter.reference import spec
    from confluence_whiteboard_exporter.snapshot import SnapshotError, board_state, load, refresh, take

    cells = spec()
    try:
        live = take(cells, board_state(cells))
    except SnapshotError as e:
        pytest.exit(f"--live: {e}", returncode=2)
    before = load(GOLDEN_DIR, cells) if (GOLDEN_DIR / "geometry.json").exists() else None
    config.stash[_LIVE] = {"live": live, "before": before}
    if config.getoption("--update-goldens"):
        config.stash[_LIVE]["changed"] = refresh(GOLDEN_DIR, cells, live)
    return live


def pytest_terminal_summary(terminalreporter, config: pytest.Config) -> None:  # noqa: ANN001 - pytest's reporter
    found = config.stash.get(_LIVE, None)
    if not found:
        return
    live, before = found["live"], found["before"]
    tr = terminalreporter
    tr.section("reference board")
    then = before.geometry.get("editor_bundle") if before else None
    tr.write_line(f"editor now: {live.geometry.get('editor_bundle')}; references from: {then or 'none'}")
    if live.missing:
        tr.write_line(f"{len(live.missing)} element(s) not found on the board: {', '.join(live.missing[:10])}")
    if "changed" in found:
        changed = found["changed"]
        if changed is None:
            tr.write_line(f"wrote new references in {GOLDEN_DIR}")
        else:
            tr.write_line(f"updated {GOLDEN_DIR}: {len(changed)} cell(s) changed"
                          + "".join(f"\n  {name}: {'; '.join(what)}" for name, what in sorted(changed.items())))
