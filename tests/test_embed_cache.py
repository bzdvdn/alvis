from __future__ import annotations

from pathlib import Path

from alvis.core.models import Chunk
from alvis.embed import (
    ApiEmbedder,
    CachingEmbedder,
    FileEmbeddingCache,
    InMemoryEmbeddingCache,
    cache_key,
)
from alvis.factories import build_embedder


class FakeEmbedder:
    """Records what it embeds; returns a per-position float vector."""

    signature = "fake:model"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, chunk: Chunk) -> list[float]:
        return (await self.embed_batch([chunk]))[0]

    async def embed_batch(self, chunks: list[Chunk]) -> list[list[float]]:
        self.calls.append([chunk.text for chunk in chunks])
        return [[float(i)] for i in range(len(chunks))]


def _chunks(*texts: str) -> list[Chunk]:
    return [Chunk(text=t, source_uri="fs:/x") for t in texts]


def test_cache_key_is_stable_and_signature_scoped() -> None:
    assert cache_key("m1", "hello world") == cache_key("m1", "hello world")
    assert cache_key("m1", "hello world") != cache_key("m2", "hello world")
    assert cache_key("m1", "hello world") != cache_key("m1", "hello")


async def test_second_batch_is_served_from_cache() -> None:
    delegate = FakeEmbedder()
    embedder = CachingEmbedder(delegate, cache=InMemoryEmbeddingCache())
    first = await embedder.embed_batch(_chunks("alpha", "beta"))
    second = await embedder.embed_batch(_chunks("alpha", "beta"))

    assert first == second == [[0.0], [1.0]]
    assert len(delegate.calls) == 1
    assert embedder.cache_hits == 2
    assert embedder.cache_misses == 2


async def test_partial_hits_preserve_order() -> None:
    delegate = FakeEmbedder()
    embedder = CachingEmbedder(delegate, cache=InMemoryEmbeddingCache())
    await embedder.embed_batch(_chunks("a", "b"))

    results = await embedder.embed_batch(_chunks("b", "a", "c"))

    assert results == [[1.0], [0.0], [0.0]]
    assert len(delegate.calls) == 2
    assert delegate.calls[-1] == ["c"]
    assert embedder.cache_hits == 2
    assert embedder.cache_misses == 3


async def test_different_signatures_do_not_share_vectors() -> None:
    cache = InMemoryEmbeddingCache()
    first = CachingEmbedder(FakeEmbedder(), cache=cache)
    await first.embed_batch(_chunks("shared text"))

    other = FakeEmbedder()
    other.signature = "fake:other-model"
    second = CachingEmbedder(other, cache=cache)
    await second.embed_batch(_chunks("shared text"))

    assert second.cache_misses == 1
    assert second.cache_hits == 0


def test_file_cache_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "embeds.cache"
    cache = FileEmbeddingCache(path)
    cache.set("k1", [0.5, 0.25])
    cache.close()

    reopened = FileEmbeddingCache(path)
    assert reopened.get("k1") == [0.5, 0.25]
    assert reopened.get("missing") is None
    reopened.close()


async def test_caching_embedder_file_cache_reuses(tmp_path: Path) -> None:
    delegate = FakeEmbedder()
    embedder = CachingEmbedder(delegate, cache=FileEmbeddingCache(tmp_path / "c"))
    await embedder.embed_batch(_chunks("persist me"))
    embedder.close()

    delegate2 = FakeEmbedder()
    embedder2 = CachingEmbedder(delegate2, cache=FileEmbeddingCache(tmp_path / "c"))
    results = await embedder2.embed_batch(_chunks("persist me"))
    embedder2.close()

    assert results == [[0.0]]
    assert len(delegate2.calls) == 0


def test_build_embedder_wraps_and_strips_cache() -> None:
    from alvis.config.models import EmbedConfig

    config = EmbedConfig(
        type="openai",
        config={
            "base_url": "http://embeddings.local/v1",
            "model": "text-embedding-3-small",
            "cache": {"path": "/tmp/alvis-embeddings.cache"},
        },
    )
    embedder = build_embedder(config)
    assert isinstance(embedder, CachingEmbedder)
    assert isinstance(embedder.delegate, ApiEmbedder)
    assert embedder.cache.path == "/tmp/alvis-embeddings.cache"

    plain = build_embedder(config, enable_cache=False)
    assert isinstance(plain, ApiEmbedder)


def test_build_embedder_in_memory_cache() -> None:
    from alvis.config.models import EmbedConfig

    config = EmbedConfig(type="default", config={"cache": True})
    embedder = build_embedder(config)
    assert isinstance(embedder, CachingEmbedder)

    disabled = EmbedConfig(type="default", config={"cache": False})
    assert not isinstance(build_embedder(disabled), CachingEmbedder)
