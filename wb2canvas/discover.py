from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator, Literal
from urllib.parse import urlsplit

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
            datetime.fromtimestamp(seconds, tz=timezone.utc)
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


def _iter_via_tree(client: httpx.Client, space_key: str) -> Iterator[BoardMeta]:
    space = _resolve_space(client, space_key)
    space_id = str(space["id"])
    home_id = space.get("homepageId")

    visited: set[tuple[str, str]] = set()
    queue: list[tuple[str, str]] = []
    if home_id:
        queue.append(("pages", str(home_id)))
    queue.extend(_top_level_children(client, space_id))

    while queue:
        kind, node_id = queue.pop()
        key = (kind, node_id)
        if key in visited:
            continue
        visited.add(key)
        for child in _direct_children(client, kind, node_id):
            ctype = child.get("type") or ""
            child_id = str(child.get("id"))
            if ctype == "whiteboard":
                yield _v2_to_board_meta(child, space_key)
                queue.append(("whiteboards", child_id))
            elif ctype == "page":
                queue.append(("pages", child_id))
            elif ctype == "folder":
                queue.append(("folders", child_id))


def _resolve_space(client: httpx.Client, space_key: str) -> dict[str, Any]:
    r = client.get("/wiki/api/v2/spaces", params={"keys": space_key, "limit": 1})
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        raise ValueError(f"Confluence space '{space_key}' not found")
    return results[0]


def _top_level_children(client: httpx.Client, space_id: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for kind, plural in (("pages", "pages"), ("folders", "folders"), ("whiteboards", "whiteboards")):
        next_path: str | None = f"/wiki/api/v2/spaces/{space_id}/{plural}"
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
                out.append((plural, str(item["id"])))
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
    nxt = (data.get("_links") or {}).get("next")
    if not nxt:
        return None, None
    parts = urlsplit(nxt)
    return parts.path, _parse_qs(parts.query)


def _parse_qs(qs: str) -> dict[str, Any]:
    if not qs:
        return {}
    out: dict[str, Any] = {}
    for pair in qs.split("&"):
        if not pair:
            continue
        k, _, v = pair.partition("=")
        out[k] = v
    return out


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
