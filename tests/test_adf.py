from __future__ import annotations

import json

import pytest

from wb2canvas.adf import adf_to_html, adf_to_markdown, safe_href


def _linked(href: str) -> str:
    return json.dumps({"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "text", "text": "click", "marks": [{"type": "link", "attrs": {"href": href}}]}]}]})


@pytest.mark.parametrize("href", ["javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,x", "vbscript:x"])
def test_unsafe_link_schemes_are_dropped_but_text_kept(href: str) -> None:
    assert "<a " not in adf_to_html(_linked(href))
    assert "click" in adf_to_html(_linked(href))
    assert adf_to_markdown(_linked(href)) == "click"


@pytest.mark.parametrize("href", ["https://example.com/a?b=1&c=2", "http://x.y", "mailto:a@b.c"])
def test_safe_link_schemes_are_kept_and_escaped(href: str) -> None:
    html = adf_to_html(_linked(href))
    assert '<a href="' in html
    assert "&amp;c=2" in html or "&" not in href
    assert safe_href(href) == href


def test_heading_with_garbage_level_does_not_crash() -> None:
    doc = json.dumps({"type": "doc", "content": [
        {"type": "heading", "attrs": {"level": "big"}, "content": [{"type": "text", "text": "T"}]}]})
    assert adf_to_markdown(doc) == "# T"
    assert adf_to_html(doc) == "<h1>T</h1>"
