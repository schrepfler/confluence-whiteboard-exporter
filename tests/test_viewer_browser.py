"""Exercise the HTML viewer's script in a real browser.

Skipped when no browser can be launched (installed Chrome is preferred so the
suite does not depend on Playwright's own, purgeable, browser download).
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from wb2canvas.board import load_board
from wb2canvas.html import render_html

sync_api = pytest.importorskip("playwright.sync_api")

FIXTURE = Path(__file__).parent / "fixtures" / "sample_dump.json"


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with sync_api.sync_playwright() as p:
        b = None
        for kwargs in ({"channel": "chrome"}, {}):
            try:
                b = p.chromium.launch(headless=True, **kwargs)
                break
            except Exception:  # noqa: BLE001 - any launch failure means "no browser here"
                continue
        if b is None:
            pytest.skip("no Chromium-based browser available")
        yield b
        b.close()


def _open(browser, tmp_path: Path, html: str):
    page = browser.new_page(viewport={"width": 1200, "height": 800})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    path = tmp_path / "board.html"
    path.write_text(html)
    page.goto(path.as_uri())
    page.wait_for_timeout(300)
    return page, errors


def test_dragging_a_node_moves_it_and_its_connector(browser, tmp_path: Path) -> None:
    page, errors = _open(browser, tmp_path, render_html(load_board(FIXTURE)))
    node = page.locator('g.wb-node[data-id="shape-A"]')
    edge = page.locator('path.wb-edge[data-src="shape-A"]')
    d_before = edge.get_attribute("d")
    box = node.bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + 80, cy + 40, steps=5)
    page.mouse.up()

    assert "translate(" in (node.get_attribute("transform") or "")
    assert edge.get_attribute("d") != d_before, "the connector follows its node"
    moved = node.bounding_box()
    assert moved["x"] == pytest.approx(box["x"] + 80, abs=2)
    assert errors == []


def test_auto_fit_grows_boxes_whose_text_overflows(browser, tmp_path: Path) -> None:
    html = render_html(load_board(FIXTURE))
    # Undersize every text area so its content must overflow.
    html = re.sub(r'(<rect [^>]*?\sheight=")[0-9.]+(")', r"\g<1>20\2", html)
    html = re.sub(r'(<foreignObject x="[^"]+" y="[^"]+" width="[^"]+" height=")[0-9.]+(">)', r"\g<1>8\2", html)
    page, errors = _open(browser, tmp_path, html)
    still_overflowing = page.evaluate(
        """() => [...document.querySelectorAll('g.wb-node rect + foreignObject div.node-text')]
              .filter(d => d.scrollHeight > d.clientHeight + 1).length"""
    )
    grown = float(page.locator('g.wb-node[data-id="shape-A"] rect').get_attribute("height"))
    assert still_overflowing == 0
    assert grown > 20
    assert errors == []


def test_wheel_zooms_around_the_cursor(browser, tmp_path: Path) -> None:
    page, errors = _open(browser, tmp_path, render_html(load_board(FIXTURE)))
    before = page.locator("svg").get_attribute("viewBox")
    page.mouse.move(600, 400)
    page.mouse.wheel(0, -300)
    page.wait_for_timeout(100)
    after = page.locator("svg").get_attribute("viewBox")
    assert float(after.split()[2]) < float(before.split()[2]), "zooming in shrinks the view"
    assert errors == []


def test_dragging_keeps_a_right_angled_connector_right_angled(browser, tmp_path: Path) -> None:
    board = load_board(FIXTURE)
    board.edges[0].routing = "dynamic"
    page, errors = _open(browser, tmp_path, render_html(board))
    edge = page.locator('path.wb-edge[data-src="shape-A"]')
    d_before = edge.get_attribute("d")
    box = page.locator('g.wb-node[data-id="shape-A"]').bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx - 30, cy + 90, steps=5)
    page.mouse.up()

    d = edge.get_attribute("d")
    assert d != d_before and " C " not in d
    nums = [float(v) for v in re.findall(r"-?[0-9.]+(?:e-?[0-9]+)?", d)]
    pts = list(zip(nums[::2], nums[1::2], strict=True))
    assert len(pts) >= 3
    assert all(abs(a[0] - b[0]) < 1e-6 or abs(a[1] - b[1]) < 1e-6 for a, b in itertools.pairwise(pts))
    assert errors == []


def _parity_board():
    from wb2canvas.board import from_dump
    from wb2canvas.model import DumpFile

    def box(x: float, y: float) -> dict:
        return {"type": "shape", "shape": 0, "position": {"x": x, "y": y}, "size": {"x": 100, "y": 60}}

    def wp(i: int, order: float, x: float, y: float) -> dict:
        return {"type": "pathWaypoint", "sourcePathIndex": i, "order": order, "position": {"x": x, "y": y}}

    els = [box(0, 0), box(400, 250), box(-300, 400)]
    cases = [  # (presentation, start cap, end cap, source anchor, target anchor, waypoints)
        (3, 1, 2, (1, 0.5), (0, 0.5), [(150, -60), (260, 300)]),
        (3, 6, 3, (0.5, 1), (0.5, 0), []),
        (3, 1, 12, (0, 0.5), (1, 0.5), [(-150, 200)]),
        (3, 8, 11, (0.5, 0), (0.5, 1), []),
        (1, 3, 14, (1, 0.5), (0, 0.5), [(200, 50)]),
        (2, 1, 5, (1, 0.5), (0.5, 0), []),
        (2, 7, 13, (0.5, 1), (1, 0.5), [(100, 300)]),
    ]
    for pr, sc, ec, sa, ta, wps in cases:
        i = len(els)
        els.append({"type": "connector", "sourceIndex": 0, "targetIndex": 1 if pr != 2 else 2, "presentation": pr,
                    "startCap": sc, "endCap": ec, "stroke": 1 + i % 3,
                    "sourceAnchor": {"left": sa[0], "top": sa[1]}, "targetAnchor": {"left": ta[0], "top": ta[1]}})
        els.extend(wp(i, n, x, y) for n, (x, y) in enumerate(wps))
    return from_dump(DumpFile.model_validate({"board": {"boardId": "p", "title": "p", "spaceKey": "S"},
                                              "strategy": "clipboard", "elements": els}))


def test_the_viewer_routes_connectors_exactly_like_the_exporter(browser, tmp_path: Path) -> None:
    html = render_html(_parity_board())
    exported = re.findall(r'<path class="wb-edge"[^>]*\sd="([^"]+)"', html)
    page, errors = _open(browser, tmp_path, html)
    redrawn = page.evaluate("() => [...document.querySelectorAll('path.wb-edge')].map(p => p.getAttribute('d'))")
    assert errors == [] and len(redrawn) == len(exported) == 7

    def numbers(d: str) -> list[float]:
        return [float(v) for v in re.findall(r"-?[0-9.]+(?:e-?[0-9]+)?", d)]

    for py, js in zip(exported, redrawn, strict=True):
        assert re.findall("[MLC]", js) == re.findall("[MLC]", py), (py, js)
        assert numbers(js) == pytest.approx(numbers(py), abs=0.06), (py, js)
