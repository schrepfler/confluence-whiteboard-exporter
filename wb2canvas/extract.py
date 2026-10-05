from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from playwright.async_api import (
    Frame,
    Page,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from .model import BoardMeta, ClipboardElement, DumpFile, FiberDump
from .storage import atomic_write_text, chrome_profile_dir, dump_path, ensure_dir, media_dir


Strategy = Literal["clipboard", "fiber", "auto"]


WHITEBOARD_URL = "{base}/wiki/spaces/{space}/whiteboard/{board}"
MEDIA_RE = re.compile(r"https?://api\.media\.atlassian\.com/file/([0-9a-f-]{36})/binary")
FRAME_PATH_FRAGMENT = "/whiteboards/whiteboard/"

_PROBE_JS = (Path(__file__).parent / "fiber_probe.js").read_text()


_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
}


def _find_chrome_binary() -> str | None:
    """Locate a real Google Chrome executable on this system."""
    candidates = []
    if sys.platform == "darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        ]
    elif sys.platform.startswith("linux"):
        candidates += ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]
    elif sys.platform == "win32":
        candidates += [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    for c in candidates:
        if Path(c).is_file():
            return c
        found = shutil.which(c)
        if found:
            return found
    return None


class SessionExpiredError(RuntimeError):
    """The saved browser session no longer authenticates."""


LOGIN_HOSTS = ("id.atlassian.com", "auth.atlassian.com")


async def login_attach(
    state_path: Path,
    base_url: str | None = None,
    port: int = 9222,
    keep_chrome: bool = True,
) -> None:
    chrome = _find_chrome_binary()
    if not chrome:
        raise RuntimeError(
            "Could not find Google Chrome. Install it (https://www.google.com/chrome) "
            "or pass --port to attach to an already-running debug-enabled Chrome."
        )

    profile_dir = chrome_profile_dir()
    target = base_url or "https://id.atlassian.com/login"

    print(f"launching real Chrome ({chrome}) on debug port {port}")
    print(f"  profile dir: {profile_dir}")
    proc = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            target,
        ]
    )

    print("\nLog in to Atlassian in the opened Chrome window.")
    print("When done, return here and press <Enter> to save the session…", flush=True)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, sys.stdin.readline)

    cdp_url = f"http://127.0.0.1:{port}"
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            raise RuntimeError(
                f"Could not connect to Chrome at {cdp_url}: {e}. "
                "Was the Chrome window closed?"
            ) from e

        contexts = browser.contexts
        if not contexts:
            await browser.close()
            raise RuntimeError("Chrome has no open contexts; open a tab first.")

        state = await contexts[0].storage_state()
        atomic_write_text(state_path, json.dumps(state, indent=2), mode=0o600)
        await browser.close()

    if not keep_chrome:
        proc.terminate()
    print(f"saved {state_path}")


async def extract_board(
    board: BoardMeta,
    base_url: str,
    out_dir: Path,
    state_path: Path,
    strategy: Strategy = "auto",
    download_media: bool = True,
    headless: bool = True,
    timeout_ms: int = 30_000,
) -> DumpFile:
    space_key = board.spaceKey
    board_id = board.boardId
    target_dump = dump_path(out_dir, space_key, board_id)
    ensure_dir(target_dump.parent)
    target_media: Path | None = None
    if download_media:
        target_media = ensure_dir(media_dir(out_dir, space_key, board_id))

    media_map: dict[str, str] = {}
    pending_media: set[asyncio.Task[None]] = set()

    def on_response(r: Response) -> None:
        task = asyncio.create_task(_capture_media(r, target_media, media_map))  # type: ignore[arg-type]
        pending_media.add(task)
        task.add_done_callback(pending_media.discard)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            storage_state=str(state_path) if state_path.exists() else None,
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = await context.new_page()

        if download_media and target_media is not None:
            page.on("response", on_response)

        url = WHITEBOARD_URL.format(
            base=base_url.rstrip("/"), space=space_key, board=board_id
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        _raise_if_login_page(page.url)

        frame = await _wait_for_canvas_frame(page, timeout_ms=timeout_ms)
        await frame.evaluate(_PROBE_JS)
        await frame.wait_for_function(
            "globalThis.__wb2canvas && globalThis.__wb2canvas.docReady && globalThis.__wb2canvas.docReady()",
            timeout=timeout_ms,
        )
        await _wait_for_doc_populated(frame, timeout_ms=10_000)

        elements: list[ClipboardElement] = []
        fiber_dump: FiberDump | None = None
        used: str = "clipboard"

        if strategy == "fiber":
            fiber_dump = await _extract_via_fiber(frame)
            used = "fiber"
        else:
            try:
                elements = await _extract_via_clipboard(page, frame)
                used = "clipboard"
            except (PlaywrightTimeoutError, RuntimeError) as e:
                if strategy == "clipboard":
                    raise
                print(f"  clipboard strategy failed ({e}); falling back to fiber", flush=True)
                fiber_dump = await _extract_via_fiber(frame)
                used = "fiber"

        # Media bodies are still streaming in; closing first would truncate or
        # drop them (large images were silently lost before).
        if pending_media:
            await asyncio.wait(set(pending_media), timeout=60)
        await browser.close()

    dump = DumpFile(
        dump_version=1,
        board=board,
        strategy=used,  # type: ignore[arg-type]
        elements=elements,
        fiber_dump=fiber_dump,
        media=media_map,
    )
    atomic_write_text(target_dump, json.dumps(dump.model_dump(exclude_none=True), indent=2))
    return dump


def _raise_if_login_page(url: str) -> None:
    if any(host in url for host in LOGIN_HOSTS):
        raise SessionExpiredError(
            "the saved Atlassian session was rejected (redirected to login); "
            "run `wb2canvas auth attach` to refresh it"
        )


async def _wait_for_canvas_frame(page: Page, timeout_ms: int) -> Frame:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000.0
    while asyncio.get_running_loop().time() < deadline:
        for fr in page.frames:
            if FRAME_PATH_FRAGMENT in fr.url:
                try:
                    await fr.wait_for_load_state("domcontentloaded", timeout=5_000)
                except PlaywrightTimeoutError:
                    pass
                return fr
        _raise_if_login_page(page.url)
        await asyncio.sleep(0.25)
    raise TimeoutError(f"Whiteboard iframe ({FRAME_PATH_FRAGMENT}) did not appear")


async def _wait_for_doc_populated(frame: Frame, timeout_ms: int) -> None:
    try:
        await frame.wait_for_function(
            "globalThis.__wb2canvas.boardSize() > 0",
            timeout=timeout_ms,
        )
    except PlaywrightTimeoutError:
        pass


async def _extract_via_clipboard(page: Page, frame: Frame) -> list[ClipboardElement]:
    await frame.evaluate("globalThis.__wb2canvas.installClipboardCapture()")

    mod = "Meta" if sys.platform == "darwin" else "Control"

    async def reset() -> None:
        await frame.evaluate("globalThis.__wb2canvas.resetCapture()")

    async def s1_canvas_press() -> None:
        canvas = frame.locator("#canvas-main")
        await canvas.click(position={"x": 8, "y": 8}, force=True, timeout=3_000)
        await page.wait_for_timeout(120)
        await canvas.press(f"{mod}+a", timeout=3_000)
        await page.wait_for_timeout(150)
        await canvas.press(f"{mod}+c", timeout=3_000)

    async def s2_body_press() -> None:
        await frame.evaluate("globalThis.__wb2canvas.focusCanvas()")
        await page.wait_for_timeout(120)
        body = frame.locator("body")
        await body.press(f"{mod}+a", timeout=3_000)
        await page.wait_for_timeout(150)
        await body.press(f"{mod}+c", timeout=3_000)

    async def s3_page_keyboard() -> None:
        await frame.evaluate("globalThis.__wb2canvas.focusCanvas()")
        await page.wait_for_timeout(120)
        await page.keyboard.press(f"{mod}+a")
        await page.wait_for_timeout(150)
        await page.keyboard.press(f"{mod}+c")

    async def s4_synthetic() -> None:
        await frame.evaluate("globalThis.__wb2canvas.dispatchSelectAllAndCopy()")

    strategies: list[tuple[str, Any]] = [
        ("canvas-locator-press", s1_canvas_press),
        ("body-locator-press", s2_body_press),
        ("page-keyboard", s3_page_keyboard),
        ("synthetic-events", s4_synthetic),
    ]

    last_err: Exception | None = None
    for name, fn in strategies:
        await reset()
        try:
            await fn()
        except Exception as e:
            last_err = e
            continue
        try:
            await frame.wait_for_function(
                "globalThis.__wb2canvas.getCapturedClipboard() !== null",
                timeout=2_500,
            )
            print(f"  clipboard strategy used: {name}", flush=True)
            break
        except PlaywrightTimeoutError as e:
            last_err = e
            continue
    else:
        raise RuntimeError(
            f"clipboard payload not captured after all strategies (last: {last_err})"
        )

    raw = await frame.evaluate("globalThis.__wb2canvas.getCapturedClipboard()")
    if isinstance(raw, dict) and "__error" in raw:
        raise RuntimeError(f"clipboard parse error: {raw['__error']}")
    if not isinstance(raw, list):
        raise RuntimeError(f"unexpected clipboard payload type: {type(raw).__name__}")
    return [ClipboardElement.model_validate(e) for e in raw]


async def _extract_via_fiber(frame: Frame) -> FiberDump:
    raw: dict[str, Any] = await frame.evaluate("globalThis.__wb2canvas.dumpYDoc()")
    return FiberDump.model_validate(raw)


async def _capture_media(response: Response, target_dir: Path, media_map: dict[str, str]) -> None:
    m = MEDIA_RE.search(response.url)
    if not m:
        return
    uuid = m.group(1)
    if uuid in media_map:
        return
    if response.status >= 400:
        return
    try:
        body = await response.body()
    except Exception:
        return
    ct = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = _MIME_EXT.get(ct, ".bin")
    out = target_dir / f"{uuid}{ext}"
    try:
        out.write_bytes(body)
    except OSError:
        return
    media_map[uuid] = f"media/{uuid}{ext}"
