from __future__ import annotations

import httpx
import pytest

from alvis.index import QdrantIndex
from alvis.sources.base import SourceError


def _mock_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def _upsert(index, text="hello", uri="u", vector=None) -> None:
    from alvis.core.models import Chunk

    await index.upsert(
        Chunk(text=text, source_uri=uri, metadata={"heading": "H"}),
        vector or [0.5, 0.5],
        source_id="confluence:TEAM@x",
        artifact_hash="hash1",
    )


async def test_qdrant_creates_collection_and_upserts() -> None:
    requests: list[tuple[str, str, bytes]] = []
    responses = {
        ("GET", "/collections/test"): httpx.Response(404),
        ("PUT", "/collections/test"): httpx.Response(200, json={}),
        ("PUT", "/collections/test/points?wait=true"): httpx.Response(200, json={}),
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        key = (
            request.method,
            str(request.url).replace("http://localhost:6333", ""),
        )
        requests.append((request.method, str(request.url), request.content))
        return responses[key]

    index = QdrantIndex(url="http://localhost:6333", collection="test")
    index.client.transport = _mock_transport(handler)

    await _upsert(index)
    assert requests[0][0] == "GET"
    assert requests[1][0] == "PUT"
    assert b'"size"' in requests[1][2]
    assert b'"vector"' in requests[2][2]
    assert b'"__source"' in requests[2][2]
    assert b'"__hash"' in requests[2][2]
    assert b'"__document_id"' in requests[2][2]
    assert b'"__uri"' in requests[2][2]
    assert b'"__schema"' in requests[2][2]
    assert b'"heading"' in requests[2][2]  # user metadata rides along


async def test_qdrant_collection_exists_skips_create() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        return httpx.Response(200, json={})

    index = QdrantIndex(url="http://localhost:6333", collection="test")
    index.client.transport = _mock_transport(handler)

    await _upsert(index)
    assert requests == ["GET", "PUT"]


async def test_qdrant_http_error_raises_source_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    index = QdrantIndex(
        url="http://localhost:6333",
        collection="test",
        retries=0,
        retry_backoff=0.0,
    )
    index.client.transport = _mock_transport(handler)

    with pytest.raises(SourceError):
        await _upsert(index)


async def test_qdrant_reconcile_deletes_stale_points() -> None:
    """One stale point (old hash) + one up-to-date point -> only stale deleted."""
    calls: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/points/scroll"):
            calls.append({"kind": "scroll", "payload": request.read()})
            return httpx.Response(
                200,
                json={
                    "result": {
                        "points": [
                            {"id": "111", "payload": {"__uri": "u/a", "__hash": "old"}},
                            {"id": "222", "payload": {"__uri": "u/b", "__hash": "new"}},
                        ],
                        "next_page_offset": None,
                    }
                },
            )
        if request.method == "POST" and request.url.path.endswith("/points/delete"):
            calls.append({"kind": "delete", "payload": request.read()})
            return httpx.Response(200, json={"result": {"status": "ok"}})
        return httpx.Response(404)

    index = QdrantIndex(url="http://localhost:6333", collection="test")
    index.client.transport = _mock_transport(handler)

    await index.reconcile("confluence:TEAM@x", {"u/a": "new", "u/b": "new"})

    deletes = [c for c in calls if c["kind"] == "delete"]
    assert len(deletes) == 1
    assert b'"111"' in deletes[0]["payload"]
    assert b'"222"' not in deletes[0]["payload"]


async def test_qdrant_reconcile_no_stale_skips_delete() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/points/scroll"):
            return httpx.Response(
                200,
                json={
                    "result": {
                        "points": [
                            {"id": "111", "payload": {"__uri": "u/a", "__hash": "h"}},
                        ],
                        "next_page_offset": None,
                    }
                },
            )
        if request.method == "POST" and request.url.path.endswith("/points/delete"):
            raise AssertionError("delete should not be called")
        return httpx.Response(404)

    index = QdrantIndex(url="http://localhost:6333", collection="test")
    index.client.transport = _mock_transport(handler)

    await index.reconcile("confluence:TEAM@x", {"u/a": "h"})


async def test_qdrant_document_id_declared_by_source() -> None:
    requests: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.content)
        return httpx.Response(200, json={})

    index = QdrantIndex(url="http://localhost:6333", collection="test")
    index.client.transport = _mock_transport(handler)

    from alvis.core.models import Chunk

    await index.upsert(
        Chunk(text="t", source_uri="https://x/a", metadata={"documentId": "blob-42"}),
        [0.5, 0.5],
        source_id="gitlab",
        artifact_hash="h",
    )
    assert b'"__document_id":"blob-42"' in requests[-1]


async def test_qdrant_document_id_falls_back_to_uri() -> None:
    requests: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.content)
        return httpx.Response(200, json={})

    index = QdrantIndex(url="http://localhost:6333", collection="test")
    index.client.transport = _mock_transport(handler)

    from alvis.core.models import Chunk

    await index.upsert(
        Chunk(text="t", source_uri="https://x/a"),
        [0.5, 0.5],
        source_id="gitlab",
        artifact_hash="h",
    )
    assert b'"__document_id":"https://x/a"' in requests[-1]


async def test_qdrant_rejects_metadata_in_reserved_namespace() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    index = QdrantIndex(url="http://localhost:6333", collection="test")
    index.client.transport = _mock_transport(handler)

    from alvis.core.models import Chunk

    with pytest.raises(ValueError, match="reserved"):
        await index.upsert(
            Chunk(text="t", source_uri="u", metadata={"__source": "nope"}),
            [0.5, 0.5],
            source_id="s",
            artifact_hash="h",
        )


async def test_qdrant_count_reports_points() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"points_count": 42}})

    index = QdrantIndex(url="http://localhost:6333", collection="test")
    index.client.transport = _mock_transport(handler)

    assert await index.count() == 42


async def test_qdrant_count_without_points_reports_zero() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {}})

    index = QdrantIndex(url="http://localhost:6333", collection="test")
    index.client.transport = _mock_transport(handler)

    assert await index.count() == 0