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
async def browser_context(state_path: Path, *, headless: bool = True) -> AsyncIterator[BrowserContext]:
    """A browser context with the saved session and clipboard access."""
    async with async_playwright() as pw:
        browser = await launch_browser(pw, headless=headless)
        try:
            yield await browser.new_context(
                storage_state=str(state_path), permissions=["clipboard-read", "clipboard-write"], viewport=VIEWPORT
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
        "globalThis.__wb2canvas.docReady && globalThis.__wb2canvas.docReady()", timeout=timeout_ms
    )
    await asyncio.sleep(READY_S)
    return page, frame


async def element_count(frame: Frame) -> int:
    return int(await frame.evaluate("globalThis.__wb2canvas.boardSize()"))


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
