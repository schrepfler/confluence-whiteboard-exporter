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


def test_follow_next_decodes_cursor_so_httpx_does_not_double_encode() -> None:
    from wb2canvas.discover import _follow_next

    path, params = _follow_next(
        {"_links": {"next": "/wiki/api/v2/pages/1/direct-children?cursor=abc%3D%3D&limit=250"}}
    )
    assert path == "/wiki/api/v2/pages/1/direct-children"
    assert params == {"cursor": "abc==", "limit": "250"}


def test_follow_next_prefixes_v1_context_relative_links() -> None:
    from wb2canvas.discover import _follow_next

    path, _ = _follow_next(
        {"_links": {"context": "/wiki", "next": "/rest/api/search?cql=x&start=100"}}
    )
    assert path == "/wiki/rest/api/search"


@respx.mock
def test_paginated_request_sends_cursor_encoded_exactly_once() -> None:
    page1 = httpx.Response(
        200,
        json={
            "results": [{"id": "WB-1", "type": "whiteboard", "title": "One"}],
            "_links": {"next": "/wiki/api/v2/pages/HOME/direct-children?cursor=eyJ9%3D%3D&limit=250"},
        },
    )
    page2 = httpx.Response(
        200, json={"results": [{"id": "WB-2", "type": "whiteboard", "title": "Two"}], "_links": {}}
    )
    route = respx.get(f"{BASE}/wiki/api/v2/pages/HOME/direct-children").mock(side_effect=[page1, page2])
    respx.get(f"{BASE}/wiki/api/v2/spaces").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "S", "key": "TEST", "homepageId": "HOME"}]})
    )
    for kind in ("pages", "folders", "whiteboards"):
        respx.get(f"{BASE}/wiki/api/v2/spaces/S/{kind}").mock(
            return_value=httpx.Response(200, json={"results": [], "_links": {}})
        )
    respx.get(url__regex=rf"{BASE}/wiki/api/v2/whiteboards/.*/direct-children").mock(
        return_value=httpx.Response(200, json={"results": [], "_links": {}})
    )

    boards = list_whiteboards_in_space(BASE, EMAIL, TOKEN, "TEST", method="tree")

    assert sorted(b.boardId for b in boards) == ["WB-1", "WB-2"]
    second_url = str(route.calls[1].request.url)
    assert "cursor=eyJ9%3D%3D" in second_url
    assert "%253D" not in second_url


def _mock_space_with_root_board(home_children: list[dict]) -> None:
    respx.get(f"{BASE}/wiki/api/v2/spaces").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "S", "key": "TEST", "homepageId": "HOME"}]})
    )
    for kind in ("pages", "folders"):
        respx.get(f"{BASE}/wiki/api/v2/spaces/S/{kind}").mock(
            return_value=httpx.Response(200, json={"results": [], "_links": {}})
        )
    respx.get(f"{BASE}/wiki/api/v2/spaces/S/whiteboards").mock(
        return_value=httpx.Response(
            200, json={"results": [{"id": "ROOT-WB", "title": "Root board", "parentId": None}], "_links": {}}
        )
    )
    respx.get(f"{BASE}/wiki/api/v2/pages/HOME/direct-children").mock(
        return_value=httpx.Response(200, json={"results": home_children, "_links": {}})
    )
    respx.get(url__regex=rf"{BASE}/wiki/api/v2/whiteboards/.*/direct-children").mock(
        return_value=httpx.Response(200, json={"results": [], "_links": {}})
    )


@respx.mock
def test_tree_walk_reports_root_level_whiteboards() -> None:
    """A board at the space root is never anyone's child; it must still be reported."""
    _mock_space_with_root_board([{"id": "NESTED-WB", "type": "whiteboard", "title": "Nested"}])

    boards = list_whiteboards_in_space(BASE, EMAIL, TOKEN, "TEST", method="tree")

    assert sorted(b.boardId for b in boards) == ["NESTED-WB", "ROOT-WB"]
    assert {b.title for b in boards} == {"Nested", "Root board"}


@respx.mock
def test_tree_walk_reports_board_reachable_twice_only_once() -> None:
    _mock_space_with_root_board([{"id": "ROOT-WB", "type": "whiteboard", "title": "Root board"}])

    boards = list_whiteboards_in_space(BASE, EMAIL, TOKEN, "TEST", method="tree")

    assert [b.boardId for b in boards] == ["ROOT-WB"]


@respx.mock
def test_get_whiteboard_unresolvable_space_raises_instead_of_empty_key() -> None:
    respx.get(f"{BASE}/wiki/api/v2/whiteboards/5").mock(
        return_value=httpx.Response(200, json={"id": "5", "title": "Orphan"})
    )
    with pytest.raises(ValueError, match="space key"):
        get_whiteboard(BASE, EMAIL, TOKEN, "5")
