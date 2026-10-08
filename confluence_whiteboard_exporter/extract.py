from __future__ import annotations

import asyncio
import base64
import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from playwright.async_api import (
    Browser,
    BrowserContext,
    Frame,
    Page,
    Playwright,
    Response,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .drawings import kind_entry
from .model import BoardMeta, ClipboardElement, DumpFile, FiberDump
from .shapes import is_icon, known
from .storage import atomic_write_text, chrome_profile_dir, dump_path, ensure_dir, media_dir


Strategy = Literal["clipboard", "fiber", "auto"]

log = logging.getLogger(__name__)


WHITEBOARD_URL = "{base}/wiki/spaces/{space}/whiteboard/{board}"
# Atlassian Media files, from the media API or the site's own proxy: the
# original (/binary) or a rendition (/image), which is what the canvas loads
# for small images.
MEDIA_RE = re.compile(r"/file/([0-9a-f-]{36})/(binary|image)(?:[/?#]|$)")
MEDIA_WAIT_S = 15.0  # how long to wait for the board's images after copying it
COPY_RETRIES = 4  # the copy leaves out images that have not loaded yet
FRAME_PATH_FRAGMENT = "/whiteboards/whiteboard/"

_PROBE_JS = (Path(__file__).parent / "fiber_probe.js").read_text()
GRAPHICS_PROBE = (Path(__file__).parent / "graphics_probe.js").read_text()


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


class Extractor:
    """Extracts boards through one shared browser.

    Launching a browser per board made whole-space runs pay a browser
    start-up for every board; this keeps one browser and context (with the
    saved session) and opens a fresh page per board.

        async with Extractor(base_url, state_path) as ex:
            for board in boards:
                await ex.extract(board, out_dir)
    """

    def __init__(
        self,
        base_url: str,
        state_path: Path,
        *,
        strategy: Strategy = "auto",
        download_media: bool = True,
        headless: bool = True,
        timeout_ms: int = 30_000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.state_path = state_path
        self.strategy = strategy
        self.download_media = download_media
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._stack = AsyncExitStack()
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> Extractor:
        pw = await self._stack.enter_async_context(async_playwright())
        browser = await launch_browser(pw, headless=self.headless)
        self._stack.push_async_callback(browser.close)
        self._context = await browser.new_context(
            storage_state=str(self.state_path) if self.state_path.exists() else None,
            permissions=["clipboard-read", "clipboard-write"],
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def extract(self, board: BoardMeta, out_dir: Path) -> DumpFile:
        if self._context is None:
            raise RuntimeError("use Extractor as an async context manager")
        target_dump = dump_path(out_dir, board.spaceKey, board.boardId)
        target_media = media_dir(out_dir, board.spaceKey, board.boardId)
        media_map: dict[str, str] = {}
        media_kinds: dict[str, str] = {}
        pending_media: set[asyncio.Task[None]] = set()

        def on_response(r: Response) -> None:
            task = asyncio.create_task(_capture_media(r, target_media, media_map, media_kinds))
            pending_media.add(task)
            task.add_done_callback(pending_media.discard)

        page = await self._context.new_page()
        try:
            if self.download_media:
                ensure_dir(target_media)
                page.on("response", on_response)
            url = WHITEBOARD_URL.format(base=self.base_url, space=board.spaceKey, board=board.boardId)
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            _raise_if_login_page(page.url)

            frame = await _wait_for_canvas_frame(page, timeout_ms=self.timeout_ms)
            await frame.evaluate(_PROBE_JS)
            await frame.wait_for_function(
                "globalThis.__whiteboardExporter && globalThis.__whiteboardExporter.docReady"
                " && globalThis.__whiteboardExporter.docReady()",
                timeout=self.timeout_ms,
            )
            await _wait_for_doc_populated(frame, timeout_ms=10_000)
            strategy, elements, fiber_dump = await self._capture(page, frame)
            art = await read_artwork(frame, elements, fiber_dump, target_media if self.download_media else None)
            images = _image_files(elements, fiber_dump)
            wanted = set(images)
            if self.download_media:
                # The canvas may still be loading images. It loads each picture
                # once, however many elements show it, so wait per picture; then
                # let any bodies still streaming finish (closing the page would
                # drop them).
                def missing() -> bool:
                    have = {images[f] for f in media_map if f in images}
                    return bool(set(images.values()) - have)

                deadline = asyncio.get_running_loop().time() + MEDIA_WAIT_S
                while missing() and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.25)
            if pending_media:
                await asyncio.wait(set(pending_media), timeout=60)
        finally:
            await page.close()
        _keep_only(media_map, wanted, target_media)

        dump = DumpFile(
            board=board,
            strategy=strategy,  # type: ignore[arg-type]
            elements=elements,
            fiber_dump=fiber_dump,
            media=media_map,
            drawings=art.drawings,
            icons=art.icons,
            stickers=art.stickers,
            stamps=art.stamps,
        )
        atomic_write_text(target_dump, json.dumps(dump.model_dump(exclude_none=True), indent=2))
        return dump

    async def _capture(
        self, page: Page, frame: Frame
    ) -> tuple[str, list[ClipboardElement], FiberDump | None]:
        if self.strategy == "fiber":
            return "fiber", [], await _extract_via_fiber(frame)
        try:
            return "clipboard", await _copy_with_images(page, frame), None
        except (PlaywrightTimeoutError, RuntimeError) as e:
            if self.strategy == "clipboard":
                raise
            log.warning("clipboard capture failed (%s); falling back to the Yjs document", e)
            return "fiber", [], await _extract_via_fiber(frame)


async def launch_browser(pw: Playwright, *, headless: bool) -> Browser:
    """Prefer the installed Google Chrome: it is already present for
    `auth attach`, and unlike Playwright's own download it is not removed
    when the OS purges caches."""
    try:
        return await pw.chromium.launch(channel="chrome", headless=headless)
    except PlaywrightError as chrome_err:
        try:
            return await pw.chromium.launch(headless=headless)
        except PlaywrightError as bundled_err:
            raise RuntimeError(
                "no browser available: install Google Chrome, or run "
                "`uv run playwright install chromium`"
                f" (chrome: {chrome_err.message.splitlines()[0]}; "
                f"bundled: {bundled_err.message.splitlines()[0]})"
            ) from bundled_err


def _raise_if_login_page(url: str) -> None:
    if any(host in url for host in LOGIN_HOSTS):
        raise SessionExpiredError(
            "the saved Atlassian session was rejected (redirected to login); "
            "run `confluence-whiteboard-exporter auth attach` to refresh it"
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
            "globalThis.__whiteboardExporter.boardSize() > 0",
            timeout=timeout_ms,
        )
    except PlaywrightTimeoutError:
        pass


async def _extract_via_clipboard(page: Page, frame: Frame) -> list[ClipboardElement]:
    await frame.evaluate("globalThis.__whiteboardExporter.installClipboardCapture()")

    mod = "Meta" if sys.platform == "darwin" else "Control"

    async def reset() -> None:
        await frame.evaluate("globalThis.__whiteboardExporter.resetCapture()")

    async def s1_canvas_press() -> None:
        canvas = frame.locator("#canvas-main")
        await canvas.click(position={"x": 8, "y": 8}, force=True, timeout=3_000)
        await page.wait_for_timeout(120)
        await canvas.press(f"{mod}+a", timeout=3_000)
        await page.wait_for_timeout(150)
        await canvas.press(f"{mod}+c", timeout=3_000)

    async def s2_body_press() -> None:
        await frame.evaluate("globalThis.__whiteboardExporter.focusCanvas()")
        await page.wait_for_timeout(120)
        body = frame.locator("body")
        await body.press(f"{mod}+a", timeout=3_000)
        await page.wait_for_timeout(150)
        await body.press(f"{mod}+c", timeout=3_000)

    async def s3_page_keyboard() -> None:
        await frame.evaluate("globalThis.__whiteboardExporter.focusCanvas()")
        await page.wait_for_timeout(120)
        await page.keyboard.press(f"{mod}+a")
        await page.wait_for_timeout(150)
        await page.keyboard.press(f"{mod}+c")

    async def s4_synthetic() -> None:
        await frame.evaluate("globalThis.__whiteboardExporter.dispatchSelectAllAndCopy()")

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
                "globalThis.__whiteboardExporter.getCapturedClipboard() !== null",
                timeout=2_500,
            )
            log.info("clipboard captured via %s", name)
            break
        except PlaywrightTimeoutError as e:
            last_err = e
            continue
    else:
        raise RuntimeError(
            f"clipboard payload not captured after all strategies (last: {last_err})"
        )

    raw = await frame.evaluate("globalThis.__whiteboardExporter.getCapturedClipboard()")
    if isinstance(raw, dict) and "__error" in raw:
        raise RuntimeError(f"clipboard parse error: {raw['__error']}")
    if not isinstance(raw, list):
        raise RuntimeError(f"unexpected clipboard payload type: {type(raw).__name__}")
    return [ClipboardElement.model_validate(e) for e in raw]


async def _extract_via_fiber(frame: Frame) -> FiberDump:
    raw: dict[str, Any] = await frame.evaluate("globalThis.__whiteboardExporter.dumpYDoc()")
    return FiberDump.model_validate(raw)


async def _capture_media(
    response: Response, target_dir: Path, media_map: dict[str, str], kinds: dict[str, str]
) -> None:
    """Save a media file the page loaded. An original replaces a rendition
    of the same file, never the other way round."""
    if "media" not in response.url:
        return
    m = MEDIA_RE.search(response.url)
    if not m:
        return
    uuid, kind = m.groups()
    if kinds.get(uuid) in ("binary", kind):
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
    previous = media_map.get(uuid)
    if previous and previous != f"media/{uuid}{ext}":
        (target_dir.parent / previous).unlink(missing_ok=True)
    media_map[uuid] = f"media/{uuid}{ext}"
    kinds[uuid] = kind


@dataclass
class Artwork:
    """What a board's shapes, icons, stickers and stamps are drawn with
    that the repo does not carry, as the dump keeps it."""

    drawings: dict[str, dict] = field(default_factory=dict)  # shape definitions, by kind
    icons: dict[str, str] = field(default_factory=dict)  # library icons' SVG files, by icon key
    stickers: dict[str, str] = field(default_factory=dict)  # stickers' images, by sticker id
    stamps: dict[str, str] = field(default_factory=dict)  # stamps' images, by stamp id


async def read_artwork(frame: Frame, elements: list[ClipboardElement], fiber_dump: FiberDump | None,
                       target_media: Path | None) -> Artwork:
    """The board's artwork, read from the editor: the definitions of its
    icon shapes and of any shape kind newer than shape_data.json; and, saved
    in `target_media` (none without it), its library icons', stickers' and
    stamps' images. What cannot be read is left out, and drawn as a
    placeholder."""
    def wanted_kind(kind: object) -> bool:
        return isinstance(kind, int) and (is_icon(kind) or not known(kind))

    kinds = {e.shape for e in elements if e.type == "shape" and wanted_kind(e.shape)}
    if fiber_dump is not None:
        kinds |= {v["sh"] for v in fiber_dump.board.values()
                  if isinstance(v, dict) and v.get("t") == "shape" and wanted_kind(v.get("sh"))}
    wanted = {(e.collection, e.category, e.iconId) for e in elements
              if e.type == "advanced-icon" and e.collection and e.category and e.iconId}
    def sprite_ids(kind: str) -> set[str]:
        ids = {e.spriteId for e in elements if e.type == kind and e.spriteId}
        if fiber_dump is not None:
            ids |= {v["si"] for v in fiber_dump.board.values()
                    if isinstance(v, dict) and v.get("t") == kind and isinstance(v.get("si"), str)}
        return ids

    sprites, stamp_ids = sprite_ids("sticker"), sprite_ids("stamp")
    art = Artwork()
    drawings, icons, stickers, stamps = art.drawings, art.icons, art.stickers, art.stamps
    if not kinds and not ((wanted or sprites or stamp_ids) and target_media):
        return art
    try:
        await frame.evaluate(GRAPHICS_PROBE)
        if kinds:
            read = await frame.evaluate("(k) => globalThis.__whiteboardExporterGraphics.shapeDrawings(k)", sorted(kinds))
            drawings.update({str(k): kind_entry(rec) for k, rec in read.items()})
        if wanted and target_media:
            svgs = await frame.evaluate(
                "(i) => globalThis.__whiteboardExporterGraphics.libraryIcons(i)",
                [{"collection": c, "category": g, "iconId": i} for c, g, i in sorted(wanted)])
            ensure_dir(target_media)
            for key, svg in svgs.items():
                name = "icon-" + re.sub(r"[^A-Za-z0-9._-]+", "-", key.replace("/", "-")) + ".svg"
                (target_media / name).write_text(svg)
                icons[key] = f"media/{name}"
        if sprites and target_media:
            paths = await frame.evaluate("() => globalThis.__whiteboardExporterGraphics.stickerImages()")
            files = await frame.evaluate("(p) => globalThis.__whiteboardExporterGraphics.download(p)",
                                         {s: paths[s] for s in sorted(sprites) if s in paths})
            ensure_dir(target_media)
            for sprite, data in files.items():
                name = "sticker-" + re.sub(r"[^A-Za-z0-9._-]+", "-", sprite) + (Path(paths[sprite]).suffix or ".webp")
                (target_media / name).write_bytes(base64.b64decode(data))
                stickers[sprite] = f"media/{name}"
        if stamp_ids and target_media:
            images = await frame.evaluate("(i) => globalThis.__whiteboardExporterGraphics.stampImages(i)",
                                          sorted(stamp_ids))
            ensure_dir(target_media)
            for stamp, url in images.items():
                if not (m := re.fullmatch(r"data:image/(png|svg)\+?[a-z]*;base64,(.*)", url, re.S)):
                    continue
                name = "stamp-" + re.sub(r"[^A-Za-z0-9._-]+", "-", stamp) + "." + m[1]
                (target_media / name).write_bytes(base64.b64decode(m[2]))
                stamps[stamp] = f"media/{name}"
    except PlaywrightError as e:
        log.warning("icon, sticker and stamp artwork could not be read (%s); they are drawn as placeholders", e)
    return art


def _image_files(elements: list[ClipboardElement], fiber_dump: FiberDump | None) -> dict[str, str]:
    """The board's image files, each with the picture it shows (its hash)."""
    files = {e.fileId: e.imageHash or e.fileId for e in elements if e.type == "image" and e.fileId}
    if fiber_dump is not None:
        for v in fiber_dump.board.values():
            if isinstance(v, dict) and v.get("t") == "image" and v.get("fi"):
                files[str(v["fi"])] = str(v.get("ih") or v["fi"])
    return files


async def _copy_with_images(page: Page, frame: Frame) -> list[ClipboardElement]:
    """Copy the board, again if the copy is missing images that are still loading."""
    expected = await frame.evaluate("globalThis.__whiteboardExporter.imageCount()")
    for attempt in range(COPY_RETRIES):
        elements = await _extract_via_clipboard(page, frame)
        got = sum(1 for e in elements if e.type == "image")
        if got >= expected:
            return elements
        log.info("copy holds %d of %d images; copying again once they load (%d)", got, expected, attempt + 1)
        await page.wait_for_timeout(3_000)
    log.warning("the copy still holds %d of %d images", got, expected)
    return elements


def _keep_only(media_map: dict[str, str], wanted: set[str], target_dir: Path) -> None:
    """Drop files the page loaded that are not the board's images (avatars, icons)."""
    for uuid in set(media_map) - wanted:
        (target_dir.parent / media_map.pop(uuid)).unlink(missing_ok=True)
