from __future__ import annotations

from winnow.core.models import Chunk
from winnow.embed import HashEmbedder
from winnow.index import MemoryIndex


async def test_embedder_deterministic_normalized() -> None:
    embedder = HashEmbedder(dimensions=64)
    chunk = Chunk(text="winnow ingestion engine", source_uri="u")
    v1 = await embedder.embed(chunk)
    v2 = await embedder.embed(chunk)
    assert v1 == v2
    assert len(v1) == 64
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-6


async def test_memory_index_stores_entries() -> None:
    index = MemoryIndex()
    chunk = Chunk(text="hello world", source_uri="u")
    await index.upsert(chunk, [0.1, 0.2])
    assert index.count == 1
    assert index.entries[0][0] is chunk