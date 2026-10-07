"""Drive a whiteboard in the browser: open it, clear it, paste into it.

Used to build the reference board (see docs/plan.md). What the editor
needs, learnt from the live editor:

- A hidden text editor holds keyboard focus when a board opens; Escape
  releases it. Otherwise a paste lands in it as text.
- It pastes from keys sent to the canvas, and deletes from keys sent to
  the page; the other way round, nothing happens.
- It reads a paste from the clipboard, as HTML carrying the payload (see
  reference.clipboard_html), and moves the pasted elements to where the
  canvas was last clicked.
- An edit takes a few seconds to reach the server; closing the page
  sooner loses it.
"""

from __future__ import annotations

import asyncio
import io
import math
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import BrowserContext, Frame, Page, async_playwright

from .extract import _PROBE_JS, WHITEBOARD_URL, _raise_if_login_page, _wait_for_canvas_frame, launch_browser

MOD = "Meta" if sys.platform == "darwin" else "Control"
SAVE_S = 4.0  # for an edit to reach the server
READY_S = 3.0  # after the document loads, for the editor to take input
VIEWPORT = {"width": 1600, "height": 1000}


@asynccontextmanager
async def browser_context(state_path: Path, *, headless: bool = True,
                          viewport: dict[str, int] = VIEWPORT) -> AsyncIterator[BrowserContext]:
    """A browser context with the saved session and clipboard access."""
    async with async_playwright() as pw:
        browser = await launch_browser(pw, headless=headless)
        try:
            yield await browser.new_context(
                storage_state=str(state_path), permissions=["clipboard-read", "clipboard-write"], viewport=viewport
            )
        finally:
            await browser.close()


async def open_board(ctx: BrowserContext, base_url: str, space: str, board_id: str,
                     timeout_ms: int = 60_000) -> tuple[Page, Frame]:
    page = await ctx.new_page()
    await page.goto(WHITEBOARD_URL.format(base=base_url.rstrip("/"), space=space, board=board_id),
                    wait_until="domcontentloaded", timeout=timeout_ms)
    _raise_if_login_page(page.url)
    frame = await _wait_for_canvas_frame(page, timeout_ms=timeout_ms)
    await frame.evaluate(_PROBE_JS)
    await frame.wait_for_function(
        "globalThis.__whiteboardExporter.docReady && globalThis.__whiteboardExporter.docReady()", timeout=timeout_ms
    )
    await asyncio.sleep(READY_S)
    return page, frame


async def element_count(frame: Frame) -> int:
    return int(await frame.evaluate("globalThis.__whiteboardExporter.boardSize()"))


async def clear_board(page: Page, frame: Frame, timeout_s: float = 30) -> None:
    """Delete every element on the board."""
    await frame.locator("#canvas-main").click(position={"x": 8, "y": 8}, force=True)
    await page.keyboard.press("Escape")
    await page.keyboard.press(f"{MOD}+a")
    await asyncio.sleep(0.3)
    await page.keyboard.press("Backspace")
    await _until(lambda: _is(frame, 0), timeout_s, "the board did not clear")
    await asyncio.sleep(SAVE_S)


async def paste(page: Page, frame: Frame, html: str, expected: int, timeout_s: float = 120) -> None:
    """Paste clipboard HTML onto the canvas and wait until the board holds
    `expected` elements and the change has been saved."""
    await frame.evaluate(
        "async (html) => { await navigator.clipboard.write("
        "[new ClipboardItem({'text/html': new Blob([html], {type: 'text/html'})})]); }",
        html,
    )
    canvas = frame.locator("#canvas-main")
    await canvas.click(position={"x": VIEWPORT["width"] // 3, "y": VIEWPORT["height"] // 3}, force=True)
    await page.keyboard.press("Escape")
    await canvas.press(f"{MOD}+v")
    await _until(lambda: _is(frame, expected), timeout_s,
                 f"the paste did not arrive (expected {expected} elements)")
    await asyncio.sleep(SAVE_S)


async def _is(frame: Frame, n: int) -> bool:
    return await element_count(frame) == n


async def _until(check, timeout_s: float, message: str) -> None:  # noqa: ANN001 - an async predicate
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not await check():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(message)
        await asyncio.sleep(0.25)


GEOMETRY_PROBE = (Path(__file__).with_name("geometry_probe.js")).read_text()


async def read_stored(frame: Frame) -> tuple[dict, dict[str, tuple[float, float]]]:
    """The board's stored elements by id, with each label's proportion
    (`pp`) and side (`pop`) folded in, and each element's stored centre."""
    dump = await frame.evaluate("globalThis.__whiteboardExporter.dumpYDoc()")
    board = dump["board"]
    centres: dict[str, tuple[float, float]] = {}
    for entry in dump["dimensions"]:
        kind, eid = entry["key"].split("#", 1)
        if kind == "p":
            centres[eid] = (entry["val"][0], entry["val"][1])
        elif kind in ("pp", "pop") and eid in board:
            board[eid][kind] = entry["val"]
    return board, centres


async def read_geometry(frame: Frame) -> dict:
    """What the editor drew: `boxes` and `paths` by element id."""
    await frame.evaluate(GEOMETRY_PROBE)
    return await frame.evaluate("globalThis.__whiteboardExporterGeometry.readAll()")


async def editor_bundle(frame: Frame) -> str:
    """The editor's main bundle, which changes with every deploy."""
    return await frame.evaluate(
        "[...document.querySelectorAll('link[rel=modulepreload][href], script[type=module][src]')]"
        ".map((e) => (e.href || e.src).split('/').pop()).find((n) => /^a1b2c3-/.test(n)) || 'unknown'"
    )


async def measure_text(frame: Frame, items: list[dict]) -> list[dict]:
    """Lay text out with the editor's own text engine: each item is
    {adf, width, fontScale}; each result {lineCount, height, contentWidth}."""
    await frame.evaluate(GEOMETRY_PROBE)
    return await frame.evaluate("(items) => globalThis.__whiteboardExporterGeometry.measure(items)", items)


# Capturing what the editor draws. A canvas much larger than this is
# stretched rather than drawn at its size, so the board is captured at 100%
# in tiles: pan, read the camera, crop every cell wholly in view.
CAPTURE_VIEWPORT = {"width": 3000, "height": 3000}
# The editor's interface floats over the canvas, inside the board's frame
# (toolbars, menus) and on the Confluence page around it (the title, the
# Share bar); both are hidden only while a tile is taken, since hiding them
# also stops the wheel from panning.
HIDE_INTERFACE = "* { visibility: hidden !important; } #canvas-main, #canvas-main * { visibility: visible !important; }"
HIDE_PAGE = "* { visibility: hidden !important; } [data-whiteboard-exporter-board] { visibility: visible !important; }"


async def read_camera(frame: Frame) -> dict[str, float]:
    await frame.evaluate(GEOMETRY_PROBE)
    return await frame.evaluate("globalThis.__whiteboardExporterGeometry.readCamera()")


async def set_zoom(page: Page, frame: Frame, percent: int) -> None:
    """Type the zoom into the editor's zoom field (a view setting only)."""
    await page.keyboard.press("Escape")
    await frame.get_by_role("button", name="Edit zoom").click()
    await page.keyboard.press(f"{MOD}+a")
    await page.keyboard.type(str(percent))
    await page.keyboard.press("Enter")
    await asyncio.sleep(1.0)


async def capture_cells(page: Page, frame: Frame, rects: dict[str, tuple[float, float, float, float]],
                        margin: float = 10) -> dict[str, bytes]:
    """PNG images of board rectangles (x, y, w, h in board units) as the
    editor draws them, at 100%, one pixel per board unit."""
    from PIL import Image

    await frame.evaluate("document.fonts.ready.then(() => true)")
    await set_zoom(page, frame, 100)
    element = await frame.frame_element()
    view = await element.bounding_box()
    assert view is not None
    await element.evaluate("(f) => f.setAttribute('data-whiteboard-exporter-board', '')")
    centre = (view["x"] + view["width"] / 2, view["y"] + view["height"] / 2)
    # Beside the frame (Confluence's sidebar), or else above it.
    away = (view["x"] / 2, centre[1]) if view["x"] >= 20 else (centre[0], view["y"] / 2)
    await page.mouse.move(*centre)
    camera = await read_camera(frame)
    scale = camera["scale"]
    if abs(scale - 1) > 0.01:
        raise RuntimeError(f"the editor would not zoom to 100% (it is at {scale:.0%})")
    left = min(x for x, _, _, _ in rects.values()) - margin
    top = min(y for _, y, _, _ in rects.values()) - margin
    right = max(x + w for x, _, w, _ in rects.values()) + margin
    bottom = max(y + h for _, y, _, h in rects.values()) + margin
    widest = max(w for _, _, w, _ in rects.values()) + 2 * margin
    tallest = max(h for _, _, _, h in rects.values()) + 2 * margin
    step_x, step_y = view["width"] / scale - widest, view["height"] / scale - tallest
    xs = [left + i * step_x for i in range(max(1, math.ceil((right - left - view["width"] / scale) / step_x) + 1))]
    ys = [top + j * step_y for j in range(max(1, math.ceil((bottom - top - view["height"] / scale) / step_y) + 1))]
    # Each tile starts at the cells' fraction of a unit, so every cell is a
    # whole number of pixels from the corner and is cropped without resampling.
    first_x, first_y = next(iter(rects.values()))[:2]
    fx, fy = first_x - math.floor(first_x), first_y - math.floor(first_y)
    images: dict[str, bytes] = {}
    for ty in ys:
        for tx in xs:
            tx, ty = math.floor(tx) + fx, math.floor(ty) + fy
            await page.mouse.move(*centre)
            for _ in range(8):  # wheel until the camera's corner is where the tile starts
                camera = await read_camera(frame)
                dx, dy = (tx - camera["left"]) * scale, (ty - camera["top"]) * scale
                if abs(dx) < 0.02 and abs(dy) < 0.02:
                    break
                await page.mouse.wheel(dx, dy)
                await asyncio.sleep(0.25)
            # Off the canvas, so no element is drawn highlighted under the pointer.
            await page.mouse.move(*away)
            await asyncio.sleep(0.6)
            camera = await read_camera(frame)
            styles = [await frame.add_style_tag(content=HIDE_INTERFACE), await page.add_style_tag(content=HIDE_PAGE)]
            await asyncio.sleep(0.15)
            shot = Image.open(io.BytesIO(await page.screenshot(clip=view))).convert("RGB")
            for style in styles:
                await style.evaluate("(s) => s.remove()")
            for name, (x, y, w, h) in rects.items():
                inside = (x >= camera["left"] + 2 and y >= camera["top"] + 2
                          and x + w <= camera["right"] - 2 and y + h <= camera["bottom"] - 2)
                if name in images or not inside:
                    continue
                px, py = (x - camera["left"]) * scale, (y - camera["top"]) * scale
                crop = shot.crop((round(px), round(py), round(px + w * scale), round(py + h * scale)))
                if crop.size != (round(w), round(h)):
                    crop = crop.resize((round(w), round(h)), Image.LANCZOS)
                buffer = io.BytesIO()
                crop.save(buffer, format="PNG", optimize=True)
                images[name] = buffer.getvalue()
    missing = set(rects) - set(images)
    if missing:
        raise RuntimeError(f"{len(missing)} cell(s) were never wholly in view: {sorted(missing)[:5]}")
    return images


async def fetch_editor_font(page: Page, frame: Frame, target: Path) -> bool:
    """Save the font the editor loads (for our renders in the report)."""
    urls = await frame.evaluate(
        "performance.getEntriesByType('resource').map((e) => e.name).filter((n) => /AtlassianSans[^/]*\\.woff2/.test(n))"
    )
    if not urls:
        return False
    response = await page.request.get(urls[0])
    if not response.ok:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await response.body())
    return True
