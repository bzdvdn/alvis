"""NotionSource — REST client against a mocked Notion API."""

from __future__ import annotations

import json

import httpx
import pytest

from alvis.sources.notion import NotionSource

_PAGE_A = {
    "object": "page",
    "id": "page-a",
    "url": "https://notion.so/page-a",
    "last_edited_time": "2026-01-01T00:00:00.000Z",
    "properties": {
        "title": {"type": "title", "title": [{"plain_text": "Alpha"}]},
    },
}
_PAGE_B = {
    "object": "page",
    "id": "page-b",
    "url": "https://notion.so/page-b",
    "last_edited_time": "2026-01-02T00:00:00.000Z",
    "properties": {
        "Name": {"type": "title", "title": [{"plain_text": "Beta"}]},
    },
}


def _source(handler) -> NotionSource:  # noqa: ANN001
    source = NotionSource(api_token_env="NOTION_TOKEN")
    source.client.transport = httpx.MockTransport(handler)
    return source


async def test_list_documents_fingerprints_by_last_edited_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["notion-version"]
        assert request.url.path == "/v1/search"
        body = json.loads(request.content)
        assert body["filter"] == {"value": "page", "property": "object"}
        return httpx.Response(200, json={"results": [_PAGE_A, _PAGE_B], "has_more": False})

    metas = await _source(handler).list_documents()

    assert [m.uri for m in metas] == ["https://notion.so/page-a", "https://notion.so/page-b"]
    assert metas[0].fingerprint == "2026-01-01T00:00:00.000Z"
    assert metas[0].content_type == "text/markdown"


async def test_list_documents_paginates_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "secret")
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if "start_cursor" not in body:
            return httpx.Response(
                200, json={"results": [_PAGE_A], "has_more": True, "next_cursor": "cur1"}
            )
        assert body["start_cursor"] == "cur1"
        return httpx.Response(200, json={"results": [_PAGE_B], "has_more": False})

    metas = await _source(handler).list_documents()

    assert calls == 2
    assert [m.uri for m in metas] == ["https://notion.so/page-a", "https://notion.so/page-b"]


async def test_fetch_renders_blocks_to_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"results": [_PAGE_A], "has_more": False})
        assert request.url.path == "/v1/blocks/page-a/children"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "type": "heading_1",
                        "has_children": False,
                        "heading_1": {"rich_text": [{"plain_text": "Title"}]},
                    },
                    {
                        "type": "paragraph",
                        "has_children": False,
                        "paragraph": {"rich_text": [{"plain_text": "Hello world."}]},
                    },
                    {
                        "type": "bulleted_list_item",
                        "has_children": False,
                        "bulleted_list_item": {"rich_text": [{"plain_text": "item one"}]},
                    },
                    {
                        "type": "code",
                        "has_children": False,
                        "code": {
                            "rich_text": [{"plain_text": "print(1)"}],
                            "language": "python",
                        },
                    },
                ],
                "has_more": False,
            },
        )

    artifacts = await _source(handler).fetch()

    assert len(artifacts) == 1
    text = artifacts[0].data.decode("utf-8")
    assert "# Title" in text
    assert "Hello world." in text
    assert "- item one" in text
    assert "```python\nprint(1)\n```" in text
    assert artifacts[0].uri == "https://notion.so/page-a"
    assert artifacts[0].content_type == "text/markdown"
    assert artifacts[0].metadata["title"] == "Alpha"
    assert artifacts[0].metadata["documentId"] == "page-a"


async def test_fetch_recurses_into_nested_blocks_and_skips_child_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"results": [_PAGE_A], "has_more": False})
        if request.url.path == "/v1/blocks/page-a/children":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "block-parent",
                            "type": "bulleted_list_item",
                            "has_children": True,
                            "bulleted_list_item": {"rich_text": [{"plain_text": "parent"}]},
                        },
                        {
                            "type": "child_page",
                            "has_children": True,
                            "child_page": {"title": "Nested Page"},
                        },
                    ],
                    "has_more": False,
                },
            )
        if request.url.path == "/v1/blocks/block-parent/children":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "type": "paragraph",
                            "has_children": False,
                            "paragraph": {"rich_text": [{"plain_text": "nested text"}]},
                        }
                    ],
                    "has_more": False,
                },
            )
        raise AssertionError(f"unexpected request to {request.url.path}")

    artifacts = await _source(handler).fetch()

    text = artifacts[0].data.decode("utf-8")
    assert "- parent" in text
    assert "nested text" in text
    assert "Nested Page" not in text


async def test_fetch_filters_by_uris(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"results": [_PAGE_A, _PAGE_B], "has_more": False})
        return httpx.Response(200, json={"results": [], "has_more": False})

    artifacts = await _source(handler).fetch(uris={"https://notion.so/page-b"})

    assert [a.uri for a in artifacts] == ["https://notion.so/page-b"]


async def test_page_title_falls_back_when_no_title_property(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "secret")
    page = {
        "id": "page-c",
        "url": "https://notion.so/page-c",
        "last_edited_time": "x",
        "properties": {},
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"results": [page], "has_more": False})
        return httpx.Response(200, json={"results": [], "has_more": False})

    artifacts = await _source(handler).fetch()

    assert artifacts[0].metadata["title"] == "Untitled"


def test_rejects_max_concurrency_below_one() -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        NotionSource(api_token_env="NOTION_TOKEN", max_concurrency=0)
