"""Our export of the reference board against what the editor drew.

One test per cell of the spec (wb2canvas/reference.py). The references in
tests/reference/golden/ come from the editor (`wb2canvas reference
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

from wb2canvas.compare import CellResult, compare
from wb2canvas.reference import payload, spec, spec_hash

GOLDEN = Path(__file__).parent / "reference" / "golden" / "geometry.json"
BOX_TOLERANCE = 1.5  # board units
PATH_TOLERANCE = 2.0

KNOWN_GAPS = {
    "free text/*": "free text is padded 8 on each side in the editor, 24 in ours, and fixed-width text "
                   "is drawn as flexible",
    "shape/60 actor": "drawings with a label below are drawn at their own aspect ratio plus the label",
    "shape/64 end": "drawings with a label below are drawn at their own aspect ratio plus the label",
    "shape/80 start": "drawings with a label below are drawn at their own aspect ratio plus the label",
    "shape/3[2-9] *": "icon shapes are drawn at their own aspect ratio plus the label below",
    "shape/4[0-9] *": "icon shapes are drawn at their own aspect ratio plus the label below",
    "shape/5[0-2] *": "icon shapes are drawn at their own aspect ratio plus the label below",
    "shape/6 upside-down-triangle": "growth to fit text uses a rectangular text area, not the shape's content box",
    "shape/13 wide": "growth to fit text uses a rectangular text area, not the shape's content box",
    "text/overflow *": "growth to fit text uses a rectangular text area, not the shape's content box",
    "icon/aws": "a library icon is drawn as a square of its basis width",
    "connector/dynamic sides (1, 0.5)->(1, 0.5)": "a right-angled route between ends on the same side "
                                                  "differs from the editor's",
}

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
        "`wb2canvas reference create --space KEY`, then run `wb2canvas reference snapshot`"
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
