from __future__ import annotations

import httpx
import pytest

from winnow.index import QdrantIndex
from winnow.sources.base import SourceError


def _mock_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def _upsert(index, text="hello", uri="u", vector=None) -> None:
    from winnow.core.models import Chunk

    await index.upsert(
        Chunk(text=text, source_uri=uri),
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
    assert b'"_source"' in requests[2][2]
    assert b'"artifact_hash"' in requests[2][2]


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
                            {"id": "111", "payload": {"source_uri": "u/a", "artifact_hash": "old"}},
                            {"id": "222", "payload": {"source_uri": "u/b", "artifact_hash": "new"}},
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
                            {"id": "111", "payload": {"source_uri": "u/a", "artifact_hash": "h"}},
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