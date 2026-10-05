from __future__ import annotations

import httpx
import pytest
import respx

from wb2canvas.discover import get_whiteboard, list_whiteboards_in_space


BASE = "https://acme.atlassian.net"
EMAIL = "me@example.com"
TOKEN = "tok"


@respx.mock
def test_cql_single_page() -> None:
    respx.get(f"{BASE}/wiki/rest/api/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "content": {
                            "id": "12345",
                            "type": "whiteboard",
                            "title": "Board One",
                            "space": {"key": "TEST"},
                            "history": {"createdDate": "2026-01-01T00:00:00.000Z"},
                        }
                    },
                    {
                        "content": {
                            "id": "99999",
                            "type": "page",
                            "title": "Some Page",
                            "space": {"key": "TEST"},
                        }
                    },
                ],
                "_links": {},
            },
        )
    )

    boards = list_whiteboards_in_space(BASE, EMAIL, TOKEN, "TEST", method="cql")
    assert len(boards) == 1
    assert boards[0].boardId == "12345"
    assert boards[0].title == "Board One"
    assert boards[0].spaceKey == "TEST"
    assert boards[0].createdAt == "2026-01-01T00:00:00.000Z"


@respx.mock
def test_cql_paginated() -> None:
    page1 = httpx.Response(
        200,
        json={
            "results": [
                {"content": {"id": "1", "type": "whiteboard", "title": "A", "space": {"key": "TEST"}}},
            ],
            "_links": {"next": "/wiki/rest/api/search?cql=&start=1&limit=1"},
        },
    )
    page2 = httpx.Response(
        200,
        json={
            "results": [
                {"content": {"id": "2", "type": "whiteboard", "title": "B", "space": {"key": "TEST"}}},
            ],
            "_links": {},
        },
    )
    respx.get(f"{BASE}/wiki/rest/api/search").mock(side_effect=[page1, page2])

    boards = list_whiteboards_in_space(BASE, EMAIL, TOKEN, "TEST", method="cql")
    assert [b.boardId for b in boards] == ["1", "2"]


@respx.mock
def test_tree_walk_finds_nested_whiteboard() -> None:
    space_resp = httpx.Response(
        200,
        json={"results": [{"id": "SPACE-ID-1", "key": "TEST", "homepageId": "HOME-1"}]},
    )
    respx.get(f"{BASE}/wiki/api/v2/spaces").mock(return_value=space_resp)

    respx.get(f"{BASE}/wiki/api/v2/spaces/SPACE-ID-1/pages").mock(
        return_value=httpx.Response(200, json={"results": [], "_links": {}})
    )
    respx.get(f"{BASE}/wiki/api/v2/spaces/SPACE-ID-1/folders").mock(
        return_value=httpx.Response(200, json={"results": [], "_links": {}})
    )
    respx.get(f"{BASE}/wiki/api/v2/spaces/SPACE-ID-1/whiteboards").mock(
        return_value=httpx.Response(200, json={"results": [], "_links": {}})
    )

    home_children = httpx.Response(
        200,
        json={
            "results": [
                {"id": "FOLDER-1", "type": "folder", "title": "F"},
                {"id": "WB-DIRECT", "type": "whiteboard", "title": "Direct WB", "parentId": "HOME-1"},
            ],
            "_links": {},
        },
    )
    respx.get(f"{BASE}/wiki/api/v2/pages/HOME-1/direct-children").mock(return_value=home_children)

    folder_children = httpx.Response(
        200,
        json={
            "results": [
                {"id": "WB-NESTED", "type": "whiteboard", "title": "Nested WB", "parentId": "FOLDER-1"},
            ],
            "_links": {},
        },
    )
    respx.get(f"{BASE}/wiki/api/v2/folders/FOLDER-1/direct-children").mock(return_value=folder_children)

    respx.get(f"{BASE}/wiki/api/v2/whiteboards/WB-DIRECT/direct-children").mock(
        return_value=httpx.Response(200, json={"results": [], "_links": {}})
    )
    respx.get(f"{BASE}/wiki/api/v2/whiteboards/WB-NESTED/direct-children").mock(
        return_value=httpx.Response(200, json={"results": [], "_links": {}})
    )

    boards = list_whiteboards_in_space(BASE, EMAIL, TOKEN, "TEST", method="tree")
    ids = sorted(b.boardId for b in boards)
    assert ids == ["WB-DIRECT", "WB-NESTED"]
    titles = {b.title for b in boards}
    assert titles == {"Direct WB", "Nested WB"}


@respx.mock
def test_tree_walk_unknown_space_raises() -> None:
    respx.get(f"{BASE}/wiki/api/v2/spaces").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    with pytest.raises(ValueError, match="not found"):
        list_whiteboards_in_space(BASE, EMAIL, TOKEN, "NOPE", method="tree")


@respx.mock
def test_get_whiteboard_returns_metadata() -> None:
    respx.get(f"{BASE}/wiki/api/v2/whiteboards/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "42",
                "type": "whiteboard",
                "title": "The Answer",
                "parentId": "100",
                "parentType": "page",
                "ownerId": "user-1",
                "createdAt": "2026-05-05T00:00:00Z",
            },
        )
    )
    b = get_whiteboard(BASE, EMAIL, TOKEN, "42", space_key="TEST")
    assert b.boardId == "42"
    assert b.title == "The Answer"
    assert b.parentId == "100"
    assert b.spaceKey == "TEST"


@respx.mock
def test_get_whiteboard_handles_epoch_ms_and_lookups_space_key() -> None:
    respx.get(f"{BASE}/wiki/api/v2/whiteboards/77").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "77",
                "type": "whiteboard",
                "title": "Numeric Date Board",
                "spaceId": "999",
                "createdAt": 1772197075710,
            },
        )
    )
    respx.get(f"{BASE}/wiki/api/v2/spaces/999").mock(
        return_value=httpx.Response(200, json={"id": "999", "key": "AUTOLOOKED"})
    )

    b = get_whiteboard(BASE, EMAIL, TOKEN, "77")
    assert b.boardId == "77"
    assert b.spaceKey == "AUTOLOOKED"
    assert b.createdAt == "2026-02-27T12:57:55.710000Z"
