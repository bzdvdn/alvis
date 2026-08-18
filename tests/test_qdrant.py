from __future__ import annotations

import httpx
import pytest

from winnow.index import QdrantIndex
from winnow.sources.base import SourceError


def _mock_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_qdrant_creates_collection_and_upserts() -> None:
    requests: list[tuple[str, str, dict]] = []
    responses = {
        ("GET", "/collections/test"): httpx.Response(404),
        ("PUT", "/collections/test"): httpx.Response(200, json={}),
        ("PUT", "/collections/test/points?wait=true"): httpx.Response(200, json={}),
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, str(request.url).replace("http://localhost:6333", ""))
        requests.append((request.method, str(request.url), request.content))
        resp = responses[key]
        return resp

    index = QdrantIndex(url="http://localhost:6333", collection="test")
    index.client.transport = _mock_transport(handler)

    from winnow.core.models import Chunk

    await index.upsert(Chunk(text="hello", source_uri="u"), [0.5, 0.5])
    assert requests[0][0] == "GET"
    assert requests[1][0] == "PUT"
    assert b'"size"' in requests[1][2]
    assert b'"vector"' in requests[2][2]


async def test_qdrant_collection_exists_skips_create() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        return httpx.Response(200, json={})

    index = QdrantIndex(url="http://localhost:6333", collection="test")
    index.client.transport = _mock_transport(handler)
    from winnow.core.models import Chunk

    await index.upsert(Chunk(text="x", source_uri="u"), [1.0])
    assert requests == ["GET", "PUT"]


async def test_qdrant_http_error_raises_source_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    index = QdrantIndex(url="http://localhost:6333", collection="test")
    index.client.transport = _mock_transport(handler)
    from winnow.core.models import Chunk

    with pytest.raises(SourceError):
        await index.upsert(Chunk(text="x", source_uri="u"), [1.0])