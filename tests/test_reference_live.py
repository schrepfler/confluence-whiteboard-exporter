"""The tester's reference board in Confluence against the references
(`pytest --live`; skipped otherwise; see tests/conftest.py).

The references in tests/reference/golden record what the editor drew.
These tests read the board again and fail for each cell the editor now
draws differently: an element moved, rerouted, gone or new, or the cell's
image changed. A failure means the editor changed, not our export; once
the change is understood, `pytest --update-goldens` takes the board as the
new references, and the offline tests then show what our export must
follow.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import pytest

from confluence_whiteboard_exporter.reference import spec
from confluence_whiteboard_exporter.snapshot import Snapshot, drift, load

GOLDEN_DIR = Path(__file__).parent / "reference" / "golden"
pytestmark = pytest.mark.live

CELLS = spec()


@cache
def _references() -> Snapshot:
    return load(GOLDEN_DIR, CELLS)


def test_the_board_holds_every_element_of_the_spec(live_snapshot: Snapshot) -> None:
    assert not live_snapshot.missing, "not found on the board: " + ", ".join(live_snapshot.missing)


@pytest.mark.parametrize("name", [c.name for c in CELLS])
def test_the_editor_still_draws_the_cell_as_recorded(live_snapshot: Snapshot, name: str) -> None:
    found = drift(_references(), live_snapshot, [name]).get(name)
    assert not found, (
        f"the editor ({live_snapshot.geometry.get('editor_bundle')}) now draws it differently from the "
        f"references ({_references().geometry.get('editor_bundle')}): " + "; ".join(found)
    )


# The icon packs whose icons were checked to export (all of each pack's
# icons read and downloaded once; see confluence-whiteboard-model.md).
KNOWN_PACKS = {"atlassian", "aws", "azure", "gcp"}


def test_every_icon_pack_the_editor_offers_can_be_read(live_snapshot: Snapshot) -> None:
    packs = live_snapshot.packs
    assert packs, "the editor's icon loader was not found, so its packs cannot be listed or read"
    unreadable = {name: pack for name, pack in packs.items()
                  if not pack or pack["with_svg"] < pack["icons"] or not pack["sample"]}
    assert not unreadable, f"icon packs the exporter cannot read (they would export as placeholders): {unreadable}"


def test_the_editor_offers_no_icon_pack_we_have_not_checked(live_snapshot: Snapshot) -> None:
    new = sorted(set(live_snapshot.packs or {}) - KNOWN_PACKS)
    assert not new, (f"the editor offers new icon packs: {new}. If they are readable (the test above) they "
                     "export already: check one on a board, then add them to KNOWN_PACKS")
