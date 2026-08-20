"""Embedding caching — reuse vectors across runs to save API calls.

The pipeline embeds every chunk on every run. For paid or rate-limited
endpoints (``openai``) an unchanged corpus would be re-embedded repeatedly;
a cache keyed by content hash + model signature serves those vectors from a
prior run for free. Only the chunks that changed or are new hit the model.

Caches are model-agnostic: keys embed the delegate's ``signature``, so a
config change (new model, new dimensions) never serves stale vectors.
"""

from __future__ import annotations

import hashlib
import shelve
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, cast

from alvis.core.models import Chunk
from alvis.embed.base import Embedder

__all__ = [
    "CachingEmbedder",
    "EmbeddingCache",
    "FileEmbeddingCache",
    "InMemoryEmbeddingCache",
]

_CACHE_GUARD = threading.Lock()
"""Serializes file-cache access across in-process pipelines (shelve/dbm is
not concurrent-safe)."""


class EmbeddingCache(Protocol):
    """Persists vectors by opaque string key."""

    def get(self, key: str) -> list[float] | None:
        """Return the cached vector for ``key``, or ``None`` on a miss."""
        ...

    def set(self, key: str, vector: list[float]) -> None:
        """Store ``vector`` under ``key``."""
        ...


class InMemoryEmbeddingCache:
    """Per-process cache (dedups within a run and across pipelines)."""

    def __init__(self) -> None:
        self._store: dict[str, list[float]] = {}

    def get(self, key: str) -> list[float] | None:
        """Return the cached vector for ``key``, or ``None`` on a miss."""
        return self._store.get(key)

    def set(self, key: str, vector: list[float]) -> None:
        """Store ``vector`` under ``key``."""
        self._store[key] = vector


class FileEmbeddingCache:
    """On-disk cache backed by the stdlib ``shelve`` module.

    Survives process restarts, so re-running a pipeline skips re-embedding
    the unchanged chunks. Concurrent access is serialized by a module-global
    lock to keep dbm files from corrupting.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        self._shelf: shelve.Shelf[Any] | None = None

    def _open(self) -> shelve.Shelf[Any]:
        if self._shelf is None:
            self._shelf = shelve.open(  # noqa: SIM115
                self.path, flag="c", writeback=False
            )
        return self._shelf

    def get(self, key: str) -> list[float] | None:
        """Return the cached vector for ``key``, or ``None`` on a miss."""
        with _CACHE_GUARD:
            value = self._open().get(key)
            if value is None:
                return None
            return list(value)

    def set(self, key: str, vector: list[float]) -> None:
        """Store ``vector`` under ``key`` and flush it to disk."""
        with _CACHE_GUARD:
            shelf = self._open()
            shelf[key] = vector
            shelf.sync()

    def close(self) -> None:
        """Close the underlying shelf, flushing any pending writes."""
        with _CACHE_GUARD:
            if self._shelf is not None:
                self._shelf.close()
                self._shelf = None


class CachingEmbedder:
    """Wraps an :class:`Embedder`, serving known texts from a cache.

    ``embed_batch`` queries the cache for every chunk, embeds only the
    misses (still batched by the delegate), and records new vectors. Input
    order is preserved. ``cache_hits`` / ``cache_misses`` expose stats for
    reporting.
    """

    def __init__(
        self,
        delegate: Embedder,
        *,
        cache: EmbeddingCache | None = None,
    ) -> None:
        self.delegate = delegate
        self.cache = cache or InMemoryEmbeddingCache()
        self.cache_hits = 0
        self.cache_misses = 0
        self.signature = delegate.signature

    async def embed(self, chunk: Chunk) -> list[float]:
        """Embed a single chunk (result of :meth:`embed_batch` on ``[chunk]``)."""
        return (await self.embed_batch([chunk]))[0]

    async def embed_batch(self, chunks: Sequence[Chunk]) -> list[list[float]]:
        """Return vectors for chunks, computing (and storing) only cache misses."""
        keys = [cache_key(self.signature, chunk.text) for chunk in chunks]
        vectors: list[list[float] | None] = [None] * len(chunks)
        missing: list[int] = []
        for i, key in enumerate(keys):
            cached = self.cache.get(key)
            if cached is None:
                self.cache_misses += 1
                missing.append(i)
            else:
                self.cache_hits += 1
                vectors[i] = cached
        if missing:
            computed = await self.delegate.embed_batch(
                [chunks[i] for i in missing]
            )
            for pos, vector in zip(missing, computed, strict=True):
                vectors[pos] = vector
                self.cache.set(keys[pos], vector)
        return [cast(list[float], vector) for vector in vectors]

    def close(self) -> None:
        """Release the underlying file cache (no-op for in-memory caches)."""
        closer = getattr(self.cache, "close", None)
        if callable(closer):
            closer()


def cache_key(signature: str, text: str) -> str:
    """Stable cache key: model signature + content hash, never text verbatim."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{signature}:{digest}"


__all__ = [
    "CachingEmbedder",
    "EmbeddingCache",
    "FileEmbeddingCache",
    "InMemoryEmbeddingCache",
    "cache_key",
]