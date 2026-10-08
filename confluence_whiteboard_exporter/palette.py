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


# Sections take their border (and title tab) and title colours from the
# palette group of their fill (the editor's getSectionBorderColor and
# getSectionTextColor): a light fill (x100) gets the hue's strong colour
# (x300) and grey text, a medium fill (x200 and the named ones) its
# strongest (x600) and white text, a strong fill (x300) its darkest (x800)
# and white text; white gets a grey border.
_MEDIUM = {"b200": "b", "teal": "t", "g150": "g", "l200": "l", "y150": "y", "orange": "o", "red": "r",
           "m200": "m", "p200": "p", "n200": "n"}
_STRONG = {f"{h}300": h for h in "btglyormp"} | {"n400": "n"}
_LIGHT = {f"{h}100": h for h in "btglyormp"}
SECTION_TEXT_GREY = "#505258"  # provisional, to be read from the reference board
SECTION_TEXT_WHITE = "#FFFFFF"


@cache
def _names() -> dict[str, tuple[str, str]]:
    """stored hex -> (palette name, drawn hex)."""
    data = json.loads(Path(__file__).with_name("palette.json").read_text())
    return {stored: (c["name"], c["value"]) for stored, c in data["colours"].items()}


def _by_name(name: str) -> str | None:
    return next((value for n, value in _names().values() if n == name), None)


def section_colours(stored_fill: str) -> tuple[str, str]:
    """The drawn colours of a section's border and of its title, from its
    stored fill: '#C8F4F9' (teal, medium) -> ('#2898BD', '#FFFFFF')."""
    name, _ = _names().get(stored_fill.upper(), ("", ""))
    if name in _LIGHT:
        return _by_name(f"{_LIGHT[name]}300") or drawn(stored_fill), SECTION_TEXT_GREY
    if name in _MEDIUM:
        return _by_name(f"{_MEDIUM[name]}600") or drawn(stored_fill), SECTION_TEXT_WHITE
    if name in _STRONG:  # darkest of the hue: provisional, x600 until the reference board says
        return _by_name(f"{_STRONG[name]}600") or drawn(stored_fill), SECTION_TEXT_WHITE
    return drawn("#B3B9C4"), SECTION_TEXT_GREY  # white, or a colour outside the palette
