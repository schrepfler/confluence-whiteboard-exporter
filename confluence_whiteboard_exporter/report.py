"""The reference report: each cell of the reference board as the editor
draws it, beside our export of it (see docs/plan.md).

Our side is the spec exported to SVG as a user's board would be, rendered
at one pixel per board unit and cropped to each cell. The editor's side is
the image `confluence-whiteboard-exporter reference snapshot` took of the
live canvas. Pass or fail comes from the geometry (compare.py); the pixels
are for people: a score, the share of drawn pixels that differ, and an
overlay marking them, so a difference can be seen before it is measured.
Icons are drawn with the artwork `reference snapshot` read from the editor
and keeps outside the repo; without it they are placeholders, marked known
rather than warned about.
"""

from __future__ import annotations

import base64
import html
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .board import from_dump, icon_key
from .compare import CellResult, compare
from .model import ClipboardElement, DumpFile
from .reference import CELL_H, Cell, Json, cell_rects, payload
from .shapes import is_icon
from .snapshot import image_path
from .svg import render_svg

BOX_TOLERANCE = 1.5  # board units; as in tests/test_reference_geometry.py
PATH_TOLERANCE = 2.0
BACKGROUND = 228  # a pixel whose channels are all this light is background (the canvas' dot grid included)
DIFFERENT = 16  # mean channel difference that makes two pixels different
WARN_SCORE = 0.15  # more than this share of drawn pixels different: worth a look


@dataclass
class Row:
    name: str
    status: str  # "pass", "warn", "known" or "fail"
    geometry: CellResult
    score: float | None
    editor_png: bytes | None
    ours_png: bytes
    diff_png: bytes | None
    note: str | None = None  # why the pixels differ, when it is known


def build(cells: list[Cell], golden: Json, image_dir: Path, font: Path | None = None,
          artwork: Json | None = None) -> list[Row]:
    from PIL import Image

    ours = render_cells(cells, font, artwork)
    notes = {c.name: placeholder(c, artwork) for c in cells}
    by_name = {c.name: c for c in cells}
    rows = []
    for result in compare(cells, golden):
        editor_path = image_path(image_dir.parent, by_name[result.name])
        mine = ours[result.name]
        score = diff_png = editor_png = None
        if editor_path.exists():
            editor_png = editor_path.read_bytes()
            score, overlay = pixel_difference(Image.open(io.BytesIO(editor_png)), mine)
            diff_png = _png(overlay)
        off = [m for m in result.measures if m.place != "caption"
               and m.difference > (PATH_TOLERANCE if m.what == "path" else BOX_TOLERANCE)]
        note = notes.get(result.name)
        status = ("fail" if off else "known" if note else
                  "warn" if score is not None and score > WARN_SCORE else "pass")
        rows.append(Row(result.name, status, result, score, editor_png, _png(mine), diff_png, note))
    order = {"fail": 0, "warn": 1, "known": 2, "pass": 3}
    rows.sort(key=lambda r: (order[r.status], -(r.score or 0.0)))
    return rows


def placeholder(cell: Cell, artwork: Json | None = None) -> str | None:
    """Why the cell is drawn as a placeholder, or None: its icon artwork
    is not in `artwork` (see load_artwork)."""
    artwork = artwork or {}
    for e in cell.elements:
        if e["type"] == "advanced-icon" and icon_key(e["collection"], e["category"], e["iconId"]) not in artwork.get("icons", {}):
            return "a library icon whose artwork was not read: a named tile stands in (run reference snapshot)"
        if e["type"] == "shape" and is_icon(e["shape"]) and str(e["shape"]) not in artwork.get("drawings", {}):
            return "an icon shape whose artwork was not read: a tile stands in (run reference snapshot)"
        if e["type"] == "sticker" and e["spriteId"] not in artwork.get("stickers", {}):
            return "a sticker whose image was not read: a tile stands in (run reference snapshot)"
        if e["type"] == "stamp" and e["spriteId"] not in artwork.get("stamps", {}):
            return "a stamp whose image was not read: a tile stands in (run reference snapshot)"
    return None


def load_artwork() -> Json | None:
    """The icon artwork `reference snapshot` read from the editor, if any."""
    from .storage import artwork_cache_path

    path = artwork_cache_path()
    return json.loads(path.read_text()) if path.exists() else None


def render_cells(cells: list[Cell], font: Path | None = None, artwork: Json | None = None) -> dict[str, object]:
    """Our export of each cell, one pixel per board unit, its icons drawn
    with `artwork` where it has them."""
    from PIL import Image
    from playwright.sync_api import sync_playwright

    elements = payload(cells)
    artwork = artwork or {}
    icons = {key: "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()
             for key, svg in artwork.get("icons", {}).items()}
    stamps = artwork.get("stamps", {})  # data URLs already
    stickers = {sprite: "data:image/webp;base64," + data for sprite, data in artwork.get("stickers", {}).items()}
    board = from_dump(DumpFile.model_validate(
        {"board": {"boardId": "reference", "title": "reference", "spaceKey": "REF"},
         "strategy": "clipboard", "elements": [ClipboardElement.model_validate(e) for e in elements],
         "drawings": artwork.get("drawings", {}), "icons": icons, "stickers": stickers, "stamps": stamps}))
    svg = render_svg(board)
    face = ""
    if font is not None and font.exists():
        data = base64.b64encode(font.read_bytes()).decode()
        face = f'<style>@font-face{{font-family:"Atlassian Sans";src:url(data:font/woff2;base64,{data})}}</style>'
    rects = cell_rects(cells)
    rows: dict[float, list[str]] = {}
    for name, (_, y, _, _) in rects.items():
        rows.setdefault(y, []).append(name)
    left = min(x for x, _, _, _ in rects.values())
    width = max(x + w for x, _, w, _ in rects.values()) - left
    out: dict[str, object] = {}
    with sync_playwright() as p:
        browser = _browser(p)
        page = browser.new_page(viewport={"width": round(width), "height": round(CELL_H)})
        page.set_content(f"<html><body style='margin:0;background:#fff'>{face}{svg}</body></html>")
        page.evaluate("document.fonts.ready")
        for y, names in rows.items():
            # Show one row of cells, one pixel per board unit.
            page.evaluate("""([x, y, w, h]) => { const s = document.querySelector('svg');
                s.setAttribute('viewBox', `${x} ${y} ${w} ${h}`); s.setAttribute('width', w); s.setAttribute('height', h); }""",
                          [left, y, width, CELL_H])
            band = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
            for name in names:
                x, _, w, h = rects[name]
                out[name] = band.crop((round(x - left), 0, round(x - left + w), round(h)))
        browser.close()
    return out


def _browser(p):  # noqa: ANN001, ANN202 - Playwright's sync API
    try:
        return p.chromium.launch(channel="chrome", headless=True)
    except Exception:  # noqa: BLE001 - fall back to Playwright's own browser
        return p.chromium.launch(headless=True)


def pixel_difference(editor, ours) -> tuple[float, object]:  # noqa: ANN001 - PIL images
    """The share of drawn pixels that differ, and an overlay marking them in
    red on a faded copy of the editor's image.

    A pixel differs only if nothing within one pixel of it in the other
    image is within DIFFERENT of it, so anti-aliasing and the sub-pixel
    placement of glyphs (the editor sets text with its own engine) do not
    count, while a mark in the wrong place does."""
    from PIL import Image, ImageChops

    a, b = (_flatten(im.convert("RGB").resize(editor.size)) for im in (editor, ours))
    changed = ImageChops.lighter(_unexplained(a, b), _unexplained(b, a))
    drawn = ImageChops.lighter(_ink(a), _ink(b))
    changed = ImageChops.multiply(changed, drawn)
    count = changed.histogram()[255]
    total = drawn.histogram()[255]
    faded = Image.blend(a, Image.new("RGB", a.size, "white"), 0.65)
    overlay = Image.composite(Image.new("RGB", a.size, (222, 53, 11)), faded, changed)
    return (count / total if total else 0.0), overlay


def _unexplained(a, b):  # noqa: ANN001, ANN202
    """Where `a` has a pixel no pixel within one of it in `b` comes close to."""
    from PIL import ImageChops, ImageFilter

    out = None
    for ca, cb in zip(a.split(), b.split(), strict=True):
        lower = ImageChops.subtract(cb.filter(ImageFilter.MinFilter(3)), _flat(cb, DIFFERENT))
        upper = ImageChops.add(cb.filter(ImageFilter.MaxFilter(3)), _flat(cb, DIFFERENT))
        outside = ImageChops.lighter(ImageChops.subtract(lower, ca), ImageChops.subtract(ca, upper))
        out = outside if out is None else ImageChops.lighter(out, outside)
    return out.point(lambda v: 255 if v > 0 else 0)


def _flat(im, value: int):  # noqa: ANN001, ANN202
    from PIL import Image

    return Image.new("L", im.size, value)


def _flatten(im):  # noqa: ANN001, ANN202
    """Background-light pixels become white, so the canvas' dot grid and
    near-white antialiasing do not count."""
    from PIL import Image

    return Image.composite(Image.new("RGB", im.size, "white"), im, _background(im))


def _background(im):  # noqa: ANN001, ANN202
    from PIL import ImageChops

    r, g, b = im.split()
    darkest = ImageChops.darker(ImageChops.darker(r, g), b)
    return darkest.point(lambda v: 255 if v >= BACKGROUND else 0)


def _ink(im):  # noqa: ANN001, ANN202
    from PIL import ImageChops

    return ImageChops.invert(_background(im))


def _png(im) -> bytes:  # noqa: ANN001
    buffer = io.BytesIO()
    im.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# --------------------------------------------------------------------- page


def write(rows: list[Row], target: Path, editor_bundle: str = "") -> None:
    counts = {s: sum(r.status == s for r in rows) for s in ("pass", "warn", "known", "fail")}
    body = "".join(_row(r) for r in rows)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_PAGE.format(
        passed=counts["pass"], warned=counts["warn"], known=counts["known"], failed=counts["fail"],
        total=len(rows),
        bundle=html.escape(editor_bundle), rows=body, box=BOX_TOLERANCE, path=PATH_TOLERANCE,
        warn=round(WARN_SCORE * 100),
    ))


def _row(r: Row) -> str:
    def img(png: bytes | None, alt: str) -> str:
        if png is None:
            return '<div class="missing">no image</div>'
        return f'<img alt="{alt}" loading="lazy" src="data:image/png;base64,{base64.b64encode(png).decode()}">'
    measures = "".join(
        f"<li class='{'off' if m.difference > (PATH_TOLERANCE if m.what == 'path' else BOX_TOLERANCE) else ''}'>"
        f"{html.escape(str(m.place))} {m.type} {m.what}: {m.difference:.1f}</li>"
        for m in r.geometry.measures if m.place != "caption")
    score = "–" if r.score is None else f"{r.score:.0%}"
    return (
        f'<section class="cell {r.status}" data-status="{r.status}">'
        f'<header><span class="badge">{r.status}</span><h2>{html.escape(r.name)}</h2>'
        f'<span class="score">pixels differing: {score}</span></header>'
        + (f'<p class="note">{html.escape(r.note)}</p>' if r.note else "") +
        f'<div class="images"><figure>{img(r.editor_png, "editor")}<figcaption>editor</figcaption></figure>'
        f'<figure>{img(r.ours_png, "ours")}<figcaption>ours</figcaption></figure>'
        f'<figure>{img(r.diff_png, "difference")}<figcaption>difference</figcaption></figure></div>'
        f'<ul class="measures">{measures}</ul></section>'
    )


_PAGE = re.sub(r"\n\s*", "\n", """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reference report</title>
<style>
:root {{ --bg: #fff; --fg: #292a2e; --muted: #6b6e76; --line: #dcdfe4; --pass: #216e4e; --warn: #9e4c00; --known: #626f86; --fail: #ae2e24; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #1f1f21; --fg: #e2e3e4; --muted: #a9abaf; --line: #3d3f43; }} }}
body {{ margin: 0; padding: 16px; background: var(--bg); color: var(--fg); font: 14px/1.4 ui-sans-serif, system-ui, sans-serif; }}
h1 {{ font-size: 20px; margin: 0 0 4px; }} .meta {{ color: var(--muted); margin: 0 0 12px; }}
.filters button {{ font: inherit; margin-right: 6px; padding: 4px 10px; border: 1px solid var(--line); border-radius: 6px; background: none; color: inherit; cursor: pointer; }}
.filters button[aria-pressed=true] {{ border-color: var(--fg); }}
.cell {{ border-top: 1px solid var(--line); padding: 12px 0; }}
.cell header {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; }}
.cell h2 {{ font-size: 15px; margin: 0; }} .score {{ color: var(--muted); }}
.badge {{ font-size: 12px; text-transform: uppercase; padding: 1px 6px; border-radius: 4px; color: #fff; }}
.pass .badge {{ background: var(--pass); }} .warn .badge {{ background: var(--warn); }} .known .badge {{ background: var(--known); }} .fail .badge {{ background: var(--fail); }}
.note {{ margin: 4px 0 0; color: var(--muted); }}
.images {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 8px; margin-top: 8px; }}
figure {{ margin: 0; }} figure img {{ width: 100%; height: auto; border: 1px solid var(--line); background: #fff; }}
figcaption {{ color: var(--muted); font-size: 12px; }} .missing {{ color: var(--muted); padding: 24px; border: 1px dashed var(--line); }}
.measures {{ columns: 2 260px; margin: 8px 0 0; padding-left: 18px; color: var(--muted); font-size: 13px; }} .measures .off {{ color: var(--fail); }}
</style></head><body>
<h1>Reference report</h1>
<p class="meta">{total} cells: {passed} pass, {warned} warn, {known} known, {failed} fail. Fail: a box off by more than {box} board units or a path by more than {path}.
Warn: more than {warn}% of drawn pixels differ. Known: drawn as a placeholder on purpose. Editor images from {bundle}.</p>
<div class="filters"><button aria-pressed="true" data-show="all">All</button><button aria-pressed="false" data-show="fail">Fail</button><button aria-pressed="false" data-show="warn">Warn</button><button aria-pressed="false" data-show="known">Known</button><button aria-pressed="false" data-show="pass">Pass</button></div>
{rows}
<script>
document.querySelectorAll('.filters button').forEach((b) => b.addEventListener('click', () => {{
  document.querySelectorAll('.filters button').forEach((o) => o.setAttribute('aria-pressed', String(o === b)));
  document.querySelectorAll('.cell').forEach((c) => {{ c.hidden = b.dataset.show !== 'all' && c.dataset.status !== b.dataset.show; }});
}}));
</script></body></html>
""")
