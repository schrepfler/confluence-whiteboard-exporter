"""Exercise the HTML viewer's script in a real browser.

Skipped when no browser can be launched (installed Chrome is preferred so the
suite does not depend on Playwright's own, purgeable, browser download).
"""

from __future__ import annotations

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
