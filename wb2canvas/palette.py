"""The colours the editor paints.

Boards store colours as RGB values of the legacy Atlassian palette. The
editor looks each value up by its palette name and paints it with that
name's design token in the current theme: a stored #172B4D is drawn
#292A2E, the grey fill #B3B9C4 is drawn #B7B9BE. palette.json comes from
the editor itself (scripts/dump_palette.js, scripts/build_palette.py).
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path


@cache
def _table() -> dict[str, str]:
    data = json.loads(Path(__file__).with_name("palette.json").read_text())
    return {stored: c["value"] for stored, c in data["colours"].items()}


def drawn(stored: str) -> str:
    """'#172B4D' -> '#292A2E'. A colour outside the palette is drawn as stored."""
    return _table().get(stored.upper(), stored.upper())
