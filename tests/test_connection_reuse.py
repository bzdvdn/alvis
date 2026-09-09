"""Connection reuse / cleanup hardening: HttpClient, QdrantIndex batch
upsert, and engine-level resource cleanup after a run.
"""

from __future__ import annotations

import httpx

from alvis import dsl, run_async
from alvis.core.models import Chunk
from alvis.index import MemoryIndex, QdrantIndex
from alvis.sources.http import HttpClient


async def test_http_client_reuses_one_async_client_across_requests() -> None:
    seen_client_ids: set[int] = set()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = HttpClient(base_url="http://x", transport=httpx.MockTransport(handler))
    for _ in range(3):
        await client.request("GET", "/ping")
        seen_client_ids.add(id(client._get_client()))

    # every call reused the exact same underlying httpx.AsyncClient instance
    assert len(seen_client_ids) == 1


async def test_http_client_aclose_releases_and_allows_reuse() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = HttpClient(base_url="http://x", transport=httpx.MockTransport(handler))
    await client.request("GET", "/ping")
    first = client._async_client
    await client.aclose()
    assert client._async_client is None

    # a subsequent request transparently reopens the pool
    await client.request("GET", "/ping")
    assert client._async_client is not None
    assert client._async_client is not first


async def test_qdrant_upsert_batch_sends_one_request_for_many_points() -> None:
    put_bodies: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404)
        if str(request.url).endswith("/collections/t"):
            return httpx.Response(200, json={})
        put_bodies.append(request.content)
        return httpx.Response(200, json={})

    index = QdrantIndex(url="http://localhost:6333", collection="t")
    index.client.transport = httpx.MockTransport(handler)

    items = [
        (Chunk(text=f"chunk {i}", source_uri=f"u/{i}"), [0.1, 0.2], "h")
        for i in range(5)
    ]
    await index.upsert_batch(items, source_id="s")

    # exactly one points PUT for all 5 chunks (well under the batch size cap)
    assert len(put_bodies) == 1
    body = put_bodies[0]
    assert body.count(b'"id"') == 5


async def test_qdrant_upsert_batch_chunks_large_batches() -> None:
    from alvis.index.qdrant import _UPSERT_BATCH_SIZE

    put_bodies: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404)
        if str(request.url).endswith("/collections/t"):
            return httpx.Response(200, json={})
        put_bodies.append(request.content)
        return httpx.Response(200, json={})

    index = QdrantIndex(url="http://localhost:6333", collection="t")
    index.client.transport = httpx.MockTransport(handler)

    total = _UPSERT_BATCH_SIZE + 1
    items = [
        (Chunk(text=f"chunk {i}", source_uri=f"u/{i}"), [0.1, 0.2], "h")
        for i in range(total)
    ]
    await index.upsert_batch(items, source_id="s")

    assert len(put_bodies) == 2
    assert put_bodies[0].count(b'"id"') == _UPSERT_BATCH_SIZE
    assert put_bodies[1].count(b'"id"') == 1


async def test_upsert_stage_uses_batch_when_indexer_supports_it(tmp_path) -> None:
    calls: list[int] = []

    class _BatchingMemoryIndex(MemoryIndex):
        async def upsert_batch(self, items, *, source_id: str) -> None:  # noqa: ANN001
            calls.append(len(items))
            for chunk, vector, artifact_hash in items:
                await self.upsert(
                    chunk, vector, source_id=source_id, artifact_hash=artifact_hash
                )

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\none\n\n# B\n\ntwo\n", encoding="utf-8")
    config = dsl.pipeline(
        dsl.fs(str(docs)),
        chunk=dsl.chunk(strategy="sections"),
        index=dsl.memory(),
    )
    indexer = _BatchingMemoryIndex()
    result = await run_async(config, indexer=indexer)

    assert calls == [result.chunks_indexed]
    assert indexer.count == result.chunks_indexed


async def test_run_async_closes_source_and_indexer_it_built(tmp_path) -> None:
    """A source/indexer built internally by the engine gets aclose()'d."""
    closed: list[str] = []

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nhello\n", encoding="utf-8")
    config = dsl.pipeline(dsl.fs(str(docs)), index=dsl.memory())

    from alvis.sources.fs import FilesystemSource

    original_aclose = getattr(FilesystemSource, "aclose", None)

    async def tracking_aclose(self) -> None:  # noqa: ANN001
        closed.append("source")
        if original_aclose is not None:
            await original_aclose(self)

    FilesystemSource.aclose = tracking_aclose  # type: ignore[attr-defined]
    try:
        await run_async(config)
    finally:
        if original_aclose is not None:
            FilesystemSource.aclose = original_aclose  # type: ignore[attr-defined]
        else:
            del FilesystemSource.aclose

    assert closed == ["source"]
