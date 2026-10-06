from __future__ import annotations

from datetime import datetime, UTC
from typing import Any, Literal
from collections.abc import Iterator
from urllib.parse import parse_qsl, urlsplit

import httpx

from .model import BoardMeta


def _coerce_str(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return str(v)


def _coerce_timestamp(v: Any) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        seconds = v / 1000.0 if v > 1e12 else float(v)
        return (
            datetime.fromtimestamp(seconds, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
    return str(v)


Method = Literal["tree", "cql"]


def make_client(base_url: str, email: str, api_token: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        auth=(email, api_token),
        headers={"Accept": "application/json"},
        timeout=30.0,
    )


def credentials_rejected(base_url: str, email: str, api_token: str) -> bool:
    """Whether Confluence refuses these credentials. It then answers most
    calls with 403, or with 404 as for an anonymous visitor, which reads
    like a missing space or board."""
    with make_client(base_url, email, api_token) as client:
        r = client.get("/wiki/rest/api/user/current")
    if r.status_code in (401, 403):
        return True
    try:
        return r.status_code == 200 and r.json().get("type") == "anonymous"
    except ValueError:
        return False


def list_whiteboards_in_space(
    base_url: str,
    email: str,
    api_token: str,
    space_key: str,
    method: Method = "tree",
) -> list[BoardMeta]:
    with make_client(base_url, email, api_token) as client:
        if method == "cql":
            return list(_iter_via_cql(client, space_key))
        return list(_iter_via_tree(client, space_key))


def get_whiteboard(
    base_url: str,
    email: str,
    api_token: str,
    board_id: str,
    space_key: str | None = None,
) -> BoardMeta:
    with make_client(base_url, email, api_token) as client:
        r = client.get(f"/wiki/api/v2/whiteboards/{board_id}")
        r.raise_for_status()
        data = r.json()
        if not space_key:
            space_id = data.get("spaceId")
            if space_id:
                sr = client.get(f"/wiki/api/v2/spaces/{space_id}")
                if sr.status_code == 200:
                    space_key = sr.json().get("key")
        if not space_key:
            raise ValueError(
                f"could not resolve the space key for whiteboard {board_id}; "
                "pass it explicitly"
            )
        return _v2_to_board_meta(data, space_key)


def _iter_via_cql(client: httpx.Client, space_key: str) -> Iterator[BoardMeta]:
    cql = f'type = "whiteboard" AND space = "{space_key}"'
    next_path: str | None = "/wiki/rest/api/search"
    next_params: dict[str, Any] | None = {"cql": cql, "limit": 100}
    while next_path:
        r = client.get(next_path, params=next_params)
        r.raise_for_status()
        data = r.json()
        for item in data.get("results", []):
            content = item.get("content") or item
            if content.get("type") != "whiteboard":
                continue
            yield _v1_to_board_meta(content, space_key)
        next_path, next_params = _follow_next(data)


_KIND_BY_TYPE = {"page": "pages", "folder": "folders", "whiteboard": "whiteboards"}


def _iter_via_tree(client: httpx.Client, space_key: str) -> Iterator[BoardMeta]:
    space = _resolve_space(client, space_key)
    space_id = str(space["id"])
    home_id = space.get("homepageId")

    visited: set[tuple[str, str]] = set()
    yielded: set[str] = set()
    queue: list[tuple[str, str, dict[str, Any]]] = []
    if home_id:
        queue.append(("pages", str(home_id), {}))
    queue.extend(_top_level_children(client, space_id))

    while queue:
        kind, node_id, item = queue.pop()
        if (kind, node_id) in visited:
            continue
        visited.add((kind, node_id))
        # Whiteboards are yielded where they are dequeued, not where they are
        # discovered, so root-level boards (which never appear as a child of
        # anything) are reported too, and boards reachable twice only once.
        if kind == "whiteboards" and node_id not in yielded:
            yielded.add(node_id)
            yield _v2_to_board_meta({"id": node_id, **item}, space_key)
        for child in _direct_children(client, kind, node_id):
            child_kind = _KIND_BY_TYPE.get(child.get("type") or "")
            if child_kind:
                queue.append((child_kind, str(child.get("id")), child))


def create_whiteboard(base_url: str, email: str, api_token: str, space_key: str, title: str) -> BoardMeta:
    """Create an empty whiteboard at the top of a space."""
    with make_client(base_url, email, api_token) as client:
        space = _resolve_space(client, space_key)
        r = client.post("/wiki/api/v2/whiteboards", json={"spaceId": space["id"], "title": title})
        r.raise_for_status()
        return _v2_to_board_meta(r.json(), space_key)


def _resolve_space(client: httpx.Client, space_key: str) -> dict[str, Any]:
    r = client.get("/wiki/api/v2/spaces", params={"keys": space_key, "limit": 1})
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        raise ValueError(f"Confluence space '{space_key}' not found")
    return results[0]


def _top_level_children(
    client: httpx.Client, space_id: str
) -> list[tuple[str, str, dict[str, Any]]]:
    out: list[tuple[str, str, dict[str, Any]]] = []
    for kind in ("pages", "folders", "whiteboards"):
        next_path: str | None = f"/wiki/api/v2/spaces/{space_id}/{kind}"
        next_params: dict[str, Any] | None = {"limit": 250}
        while next_path:
            r = client.get(next_path, params=next_params)
            if r.status_code == 404:
                break
            r.raise_for_status()
            data = r.json()
            for item in data.get("results", []):
                if item.get("parentId"):
                    continue
                out.append((kind, str(item["id"]), item))
            next_path, next_params = _follow_next(data)
    return out


def _direct_children(client: httpx.Client, kind: str, node_id: str) -> Iterator[dict[str, Any]]:
    next_path: str | None = f"/wiki/api/v2/{kind}/{node_id}/direct-children"
    next_params: dict[str, Any] | None = {"limit": 250}
    while next_path:
        r = client.get(next_path, params=next_params)
        if r.status_code == 404:
            return
        r.raise_for_status()
        data = r.json()
        yield from data.get("results", [])
        next_path, next_params = _follow_next(data)


def _follow_next(data: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve a `_links.next` cursor into (path, decoded params).

    v2 returns `/wiki/api/v2/...`; v1 returns a path relative to the
    `/wiki` context (`/rest/api/search?...`), so it must be re-prefixed.
    Query values are percent-decoded here because httpx re-encodes them —
    passing them through raw double-encodes base64 cursors (`%3D` → `%253D`).
    """
    links = data.get("_links") or {}
    nxt = links.get("next")
    if not nxt:
        return None, None
    parts = urlsplit(nxt)
    path = parts.path
    context = (links.get("context") or "/wiki").rstrip("/")
    if context and not path.startswith(context + "/"):
        path = context + path
    return path, dict(parse_qsl(parts.query, keep_blank_values=True))


def _v1_to_board_meta(content: dict[str, Any], space_key: str) -> BoardMeta:
    space = content.get("space") or {}
    history = content.get("history") or {}
    return BoardMeta(
        boardId=str(content["id"]),
        contentId=str(content["id"]),
        title=content.get("title") or "",
        spaceKey=_coerce_str(space.get("key")) or space_key,
        parentId=None,
        parentType=None,
        ownerId=_coerce_str((history.get("createdBy") or {}).get("accountId")),
        createdAt=_coerce_timestamp(history.get("createdDate")),
    )


def _v2_to_board_meta(item: dict[str, Any], space_key: str | None) -> BoardMeta:
    return BoardMeta(
        boardId=str(item["id"]),
        contentId=str(item["id"]),
        title=item.get("title") or "",
        spaceKey=space_key or "",
        parentId=_coerce_str(item.get("parentId")),
        parentType=_coerce_str(item.get("parentType")),
        ownerId=_coerce_str(item.get("ownerId") or item.get("authorId")),
        createdAt=_coerce_timestamp(item.get("createdAt")),
    )
