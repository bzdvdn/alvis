from __future__ import annotations

from alvis.core.models import Chunk
from alvis.embed import HashEmbedder
from alvis.index import MemoryIndex


async def test_embedder_deterministic_normalized() -> None:
    embedder = HashEmbedder(dimensions=64)
    chunk = Chunk(text="alvis ingestion engine", source_uri="u")
    v1 = await embedder.embed(chunk)
    v2 = await embedder.embed(chunk)
    assert v1 == v2
    assert len(v1) == 64
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-6


async def test_memory_index_stores_entries() -> None:
    index = MemoryIndex()
    chunk = Chunk(text="hello world", source_uri="u")
    await index.upsert(chunk, [0.1, 0.2], source_id="fs:/x", artifact_hash="a")
    assert index.count == 1
    assert index.entries()[0][0] is chunk


async def test_memory_index_deterministic_ids_overwrite() -> None:
    index = MemoryIndex()
    chunk = Chunk(text="same text", source_uri="u")
    await index.upsert(chunk, [0.1], source_id="fs:/x", artifact_hash="a")
    await index.upsert(chunk, [0.9], source_id="fs:/x", artifact_hash="a")
    assert index.count == 1
    assert index.entries()[0][1] == [0.9]


async def test_memory_index_reconcile_prunes_stale() -> None:
    index = MemoryIndex()
    changed = Chunk(text="old text", source_uri="u/changed")
    gone = Chunk(text="gone", source_uri="u/gone")
    kept = Chunk(text="kept", source_uri="u/kept")
    await index.upsert(changed, [0.1], source_id="fs:/x", artifact_hash="oldhash")
    await index.upsert(gone, [0.2], source_id="fs:/x", artifact_hash="h")
    await index.upsert(kept, [0.3], source_id="fs:/x", artifact_hash="h")
    # unrelated source must not be touched
    await index.upsert(changed, [0.4], source_id="fs:/y", artifact_hash="oldhash")

    await index.reconcile("fs:/x", {"u/changed": "newhash", "u/kept": "h"})

    remaining = {
        (p.source_id, p.chunk.source_uri, p.artifact_hash) for p in index.points.values()
    }
    assert remaining == {
        ("fs:/x", "u/kept", "h"),
        ("fs:/y", "u/changed", "oldhash"),
    }