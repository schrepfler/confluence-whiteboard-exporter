"""Our export of the reference board against what the editor drew.

One test per cell of the spec (confluence_whiteboard_exporter/reference.py). The references in
tests/reference/golden/ come from the editor (`confluence-whiteboard-exporter reference
snapshot`); these tests need no Confluence. A cell passes when each of its
elements is within tolerance: its box within BOX_TOLERANCE on every edge,
its path within PATH_TOLERANCE everywhere. Captions are only scaffolding
and do not count.

KNOWN_GAPS lists the cells we do not draw like the editor yet, with why.
They are expected to fail; one that starts passing fails the run, so the
list stays true.
"""

from __future__ import annotations

import fnmatch
import json
from functools import cache
from pathlib import Path

import pytest

from confluence_whiteboard_exporter.compare import CellResult, compare
from confluence_whiteboard_exporter.reference import payload, spec, spec_hash

GOLDEN = Path(__file__).parent / "reference" / "golden" / "geometry.json"
BOX_TOLERANCE = 1.5  # board units
PATH_TOLERANCE = 2.0

KNOWN_GAPS: dict[str, str] = {}  # cell name pattern: why we do not draw it like the editor yet

CELLS = spec()


@cache
def _results() -> dict[str, CellResult]:
    return {r.name: r for r in compare(CELLS, json.loads(GOLDEN.read_text()))}


def _gap(name: str) -> str | None:
    return next((why for pattern, why in KNOWN_GAPS.items() if fnmatch.fnmatchcase(name, pattern)), None)


def test_the_references_are_for_this_spec() -> None:
    golden = json.loads(GOLDEN.read_text())
    assert golden["spec_hash"] == spec_hash(payload(CELLS)), (
        "the spec changed since the references were taken: rebuild the board with "
        "`confluence-whiteboard-exporter reference create --space KEY`, "
        "then run `confluence-whiteboard-exporter reference snapshot`"
    )
    assert set(golden["cells"]) == {c.name for c in CELLS}
    assert not [e for entries in golden["cells"].values() for e in entries if e.get("missing")]


@pytest.mark.parametrize(
    "name",
    [pytest.param(c.name, marks=pytest.mark.xfail(reason=why, strict=True)) if (why := _gap(c.name)) else c.name
     for c in CELLS],
)
def test_cell_matches_the_editor(name: str) -> None:
    result = _results()[name]
    off = [f"{m.type} #{m.place} {m.what} off by {m.difference:.1f}"
           for m in result.measures
           if m.place != "caption" and m.difference > (PATH_TOLERANCE if m.what == "path" else BOX_TOLERANCE)]
    assert result.measures, "nothing to compare"
    assert not off, "; ".join(off)


def test_every_known_gap_names_a_cell() -> None:
    names = [c.name for c in CELLS]
    assert all(any(fnmatch.fnmatchcase(n, p) for n in names) for p in KNOWN_GAPS)
