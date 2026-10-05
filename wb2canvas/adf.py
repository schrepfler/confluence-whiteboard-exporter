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
        level = int(node.get("attrs", {}).get("level", 1))
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
            href = m.get("attrs", {}).get("href", "")
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
        level = max(1, min(6, int(node.get("attrs", {}).get("level", 1))))
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
            href = _xml_escape(m.get("attrs", {}).get("href", ""))
            text = f'<a href="{href}">{text}</a>'
    return text


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
