"""ElasticsearchIndex — REST client against a mocked Elasticsearch API."""

from __future__ import annotations

import json

import httpx
import pytest

from alvis.core.models import Chunk
from alvis.index import ElasticsearchIndex


def _index(handler) -> ElasticsearchIndex:  # noqa: ANN001
    index = ElasticsearchIndex(url="http://localhost:9200", index="alvis_docs")
    index.client.transport = httpx.MockTransport(handler)
    return index


async def test_upsert_creates_index_then_puts_document() -> None:
    calls: list[tuple[str, str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, request.url.path, body))
        if request.method == "GET":
            return httpx.Response(404, json={})
        return httpx.Response(200, json={})

    index = _index(handler)
    await index.upsert(
        Chunk(text="hello world", source_uri="u/1", metadata={"space": "ENG"}),
        [0.1, 0.2],
        source_id="s",
        artifact_hash="h",
    )

    methods = [(m, p) for m, p, _ in calls]
    assert ("GET", "/alvis_docs") in methods
    assert ("PUT", "/alvis_docs") in methods
    put_doc = next(b for m, p, b in calls if m == "PUT" and p != "/alvis_docs")
    assert put_doc["__text"] == "hello world"
    assert put_doc["__uri"] == "u/1"
    assert put_doc["space"] == "ENG"
    assert put_doc["vector"] == [0.1, 0.2]

    mapping = next(b for m, p, b in calls if m == "PUT" and p == "/alvis_docs")
    assert mapping["mappings"]["properties"]["vector"]["type"] == "dense_vector"
    assert mapping["mappings"]["properties"]["vector"]["dims"] == 2
    assert mapping["mappings"]["properties"]["vector"]["similarity"] == "cosine"


async def test_upsert_skips_index_creation_when_it_already_exists() -> None:
    put_index_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal put_index_calls
        if request.method == "GET":
            return httpx.Response(200, json={})
        if request.method == "PUT" and request.url.path == "/alvis_docs":
            put_index_calls += 1
        return httpx.Response(200, json={})

    index = _index(handler)
    await index.upsert(
        Chunk(text="a", source_uri="u/1"), [0.1], source_id="s", artifact_hash="h"
    )

    assert put_index_calls == 0


async def test_upsert_rejects_reserved_metadata_keys() -> None:
    index = ElasticsearchIndex(url="http://localhost:9200", index="alvis_docs")
    with pytest.raises(ValueError, match="reserved"):
        await index.upsert(
            Chunk(text="a", source_uri="u/1", metadata={"__hack": "x"}),
            [0.1],
            source_id="s",
            artifact_hash="h",
        )


async def test_search_sends_knn_query_and_excludes_vector_from_metadata() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {
                            "_score": 0.92,
                            "_source": {
                                "vector": [0.1, 0.2],
                                "__text": "hello",
                                "__uri": "u/1",
                                "space": "ENG",
                            },
                        }
                    ]
                }
            },
        )

    index = _index(handler)
    hits = await index.search([0.1, 0.2], top_k=3)

    assert captured["body"]["knn"]["field"] == "vector"
    assert captured["body"]["knn"]["k"] == 3
    assert captured["body"]["_source"]["excludes"] == ["vector"]
    assert len(hits) == 1
    assert hits[0].text == "hello"
    assert hits[0].source_uri == "u/1"
    assert hits[0].score == 0.92
    assert hits[0].metadata == {"space": "ENG"}
    assert "vector" not in hits[0].metadata


async def test_search_missing_index_returns_empty() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "index_not_found_exception"})

    index = _index(handler)
    assert await index.search([0.1], top_k=3) == []


async def test_search_builds_filters_and_acl_should_clause() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"hits": {"hits": []}})

    index = _index(handler)
    await index.search([0.1], top_k=3, filters={"space": "ENG"}, principals=["eng"])

    clauses = captured["body"]["knn"]["filter"]
    assert {"term": {"space": "ENG"}} in clauses
    acl_clause = next(c for c in clauses if "bool" in c)
    should = acl_clause["bool"]["should"]
    assert {"terms": {"__acl": ["eng"]}} in should
    assert any("must_not" in c.get("bool", {}) for c in should)


async def test_keyword_search_uses_match_query() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {
                            "_score": 5.1,
                            "_source": {"__text": "fox fox fox", "__uri": "u/1"},
                        }
                    ]
                }
            },
        )

    index = _index(handler)
    hits = await index.keyword_search("fox", top_k=5)

    assert captured["body"]["query"]["bool"]["must"] == [{"match": {"__text": "fox"}}]
    assert hits[0].score == 5.1


async def test_keyword_search_missing_index_returns_empty() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    index = _index(handler)
    assert await index.keyword_search("fox", top_k=5) == []


async def test_reconcile_deletes_stale_documents_across_pages() -> None:
    page1 = {
        "hits": {
            "hits": [
                {"_id": "a", "_source": {"__uri": "u/keep", "__hash": "h1"}, "sort": [1]},
                {"_id": "b", "_source": {"__uri": "u/stale", "__hash": "h2"}, "sort": [2]},
            ]
        }
    }
    page2 = {"hits": {"hits": []}}
    requests: list[tuple[str, str]] = []
    deleted: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "DELETE":
            deleted.append(request.url.path)
            return httpx.Response(200, json={})
        body = json.loads(request.content)
        if "search_after" in body:
            return httpx.Response(200, json=page2)
        return httpx.Response(200, json=page1)

    index = _index(handler)
    await index.reconcile("s", {"u/keep": "h1"})

    assert deleted == ["/alvis_docs/_doc/b"]


async def test_reconcile_missing_index_is_noop() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    index = _index(handler)
    await index.reconcile("s", {})  # must not raise


async def test_count_returns_document_count() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/alvis_docs/_count"
        return httpx.Response(200, json={"count": 7})

    index = _index(handler)
    assert await index.count() == 7


async def test_count_missing_index_returns_zero() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    index = _index(handler)
    assert await index.count() == 0
