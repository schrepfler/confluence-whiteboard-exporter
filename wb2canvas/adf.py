from __future__ import annotations

import json
from typing import Any


def adf_to_markdown(adf: dict[str, Any] | str | None) -> str:
    if adf is None:
        return ""
    if isinstance(adf, str):
        try:
            adf = json.loads(adf)
        except json.JSONDecodeError:
            return adf
    if not isinstance(adf, dict):
        return ""
    return _render_node(adf).rstrip()


def _render_node(node: dict[str, Any]) -> str:
    t = node.get("type")
    children = node.get("content") or []

    if t == "doc":
        return "\n\n".join(_render_node(c) for c in children)
    if t == "paragraph":
        return "".join(_render_node(c) for c in children)
    if t == "heading":
        level = _heading_level(node)
        return "#" * level + " " + "".join(_render_node(c) for c in children)
    if t == "text":
        text = node.get("text", "")
        return _apply_marks(text, node.get("marks") or [])
    if t == "hardBreak":
        return "  \n"
    if t == "bulletList":
        return "\n".join("- " + _render_node(c) for c in children)
    if t == "orderedList":
        return "\n".join(f"{i + 1}. " + _render_node(c) for i, c in enumerate(children))
    if t == "listItem":
        return "".join(_render_node(c) for c in children)
    if t == "codeBlock":
        lang = node.get("attrs", {}).get("language", "")
        body = "".join(_render_node(c) for c in children)
        return f"```{lang}\n{body}\n```"
    if t == "blockquote":
        body = "".join(_render_node(c) for c in children)
        return "\n".join("> " + line for line in body.splitlines())
    return "".join(_render_node(c) for c in children if isinstance(c, dict))


_SAFE_SCHEMES = ("http", "https", "mailto")


def safe_href(href: str) -> str | None:
    """Return href if its scheme is allowlisted, else None.

    Link targets come from board content that anyone with edit access can
    author, and the SVG output executes script, so a `javascript:` link
    would run in the viewer's browser when clicked.
    """
    if not isinstance(href, str):
        return None
    scheme, sep, _ = href.strip().partition(":")
    if sep and scheme.lower() in _SAFE_SCHEMES:
        return href.strip()
    return None


def _heading_level(node: dict[str, Any]) -> int:
    try:
        return max(1, min(6, int((node.get("attrs") or {}).get("level", 1))))
    except (TypeError, ValueError):
        return 1


def _apply_marks(text: str, marks: list[dict[str, Any]]) -> str:
    for m in marks:
        mt = m.get("type")
        if mt == "strong":
            text = f"**{text}**"
        elif mt == "em":
            text = f"*{text}*"
        elif mt == "code":
            text = f"`{text}`"
        elif mt == "strike":
            text = f"~~{text}~~"
        elif mt == "link":
            href = safe_href(m.get("attrs", {}).get("href", ""))
            if href:
                text = f"[{text}]({href})"
    return text


def adf_to_html(adf: dict[str, Any] | str | None) -> str:
    if adf is None:
        return ""
    if isinstance(adf, str):
        try:
            adf = json.loads(adf)
        except json.JSONDecodeError:
            return _xml_escape(adf)
    if not isinstance(adf, dict):
        return ""
    return _render_node_html(adf)


def _render_node_html(node: dict[str, Any]) -> str:
    t = node.get("type")
    children = node.get("content") or []

    if t == "doc":
        return "".join(_render_node_html(c) for c in children)
    if t == "paragraph":
        body = "".join(_render_node_html(c) for c in children)
        return f"<p>{body}</p>"
    if t == "heading":
        level = _heading_level(node)
        body = "".join(_render_node_html(c) for c in children)
        return f"<h{level}>{body}</h{level}>"
    if t == "text":
        text = _xml_escape(node.get("text", ""))
        return _apply_marks_html(text, node.get("marks") or [])
    if t == "hardBreak":
        return "<br/>"
    if t == "bulletList":
        return "<ul>" + "".join(_render_node_html(c) for c in children) + "</ul>"
    if t == "orderedList":
        return "<ol>" + "".join(_render_node_html(c) for c in children) + "</ol>"
    if t == "listItem":
        return "<li>" + "".join(_render_node_html(c) for c in children) + "</li>"
    if t == "codeBlock":
        body = "".join(_render_node_html(c) for c in children)
        return f"<pre><code>{body}</code></pre>"
    if t == "blockquote":
        return "<blockquote>" + "".join(_render_node_html(c) for c in children) + "</blockquote>"
    return "".join(_render_node_html(c) for c in children if isinstance(c, dict))


def _apply_marks_html(text: str, marks: list[dict[str, Any]]) -> str:
    for m in marks:
        mt = m.get("type")
        if mt == "strong":
            text = f"<strong>{text}</strong>"
        elif mt == "em":
            text = f"<em>{text}</em>"
        elif mt == "code":
            text = f"<code>{text}</code>"
        elif mt == "strike":
            text = f"<s>{text}</s>"
        elif mt == "link":
            href = safe_href(m.get("attrs", {}).get("href", ""))
            if href:
                text = f'<a href="{_xml_escape(href)}">{text}</a>'
    return text


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
