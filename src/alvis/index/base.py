"""Indexer protocol."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from alvis.core.models import Chunk, SearchHit


class Indexer(Protocol):
    """Persists chunk vectors into a store with idempotent semantics."""

    async def upsert(
        self,
        chunk: Chunk,
        vector: list[float],
        *,
        source_id: str,
        artifact_hash: str,
    ) -> None:
        """Insert or replace a chunk point.

        Implementations must make point IDs deterministic so identical content
        is overwritten rather than duplicated.
        """
        ...

    async def reconcile(
        self,
        source_id: str,
        current: Mapping[str, str],
    ) -> None:
        """Delete this source's points not present in ``current``.

        ``current`` maps ``source_uri -> artifact_hash`` from the latest run;
        stale points (changed or removed documents) are pruned.
        """
        ...

    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """Return the ``top_k`` closest chunks to ``vector``, best first.

        Scores are cosine similarities in ``[0, 1]``; implementations must
        sort results so the highest score comes first. ``filters`` restricts
        candidates to points whose metadata matches every ``key: value`` pair
        (exact equality, applied before ranking) — e.g. ``{"space": "ENG"}``.

        ``principals`` is the caller's identity (user id, group names, ...).
        When given, a point is visible only if it carries no ``__acl`` (public)
        or its ``__acl`` intersects ``principals``; a point with an ``__acl``
        and no matching principal is excluded, never merely down-ranked.
        ``principals=None`` (the default) applies no ACL filtering at all —
        opt in explicitly once callers pass real identity.
        """
        ...


class KeywordIndexer(Protocol):
    """Optional capability: lexical (BM25) search alongside vector search.

    Backends that keep the full chunk text locally (``memory``, ``sqlite``)
    can implement this so ``query(..., hybrid=True)`` fuses a dense ranking
    with a keyword ranking (see :mod:`alvis.index.fusion`). Backends without
    it (``qdrant``, ``pgvector`` — text lives server-side with no BM25
    endpoint behind this minimal client) degrade to vector-only search under
    ``hybrid=True``; that's a logged fallback, not an error, since a plugin
    may add native full-text search of its own later.
    """

    async def keyword_search(
        self,
        text: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """Return the ``top_k`` chunks with the highest BM25 score, best first.

        ``principals`` filters by ``__acl`` exactly as in :meth:`Indexer.search`.
        """
        ...


class BatchIndexer(Protocol):
    """Optional capability: upsert many chunks in one round trip.

    Backends whose write is a network call per point (``qdrant``) benefit
    from sending many in a single request instead of the default per-chunk
    ``upsert`` loop the engine otherwise runs. Backends where "batching"
    would not actually save a round trip (``memory``, ``sqlite`` — already
    in-process; ``pgvector`` — one connection reused across calls, see
    :mod:`alvis.index.pgvector`) don't need to implement this; the engine
    falls back to the per-chunk loop when it's absent.
    """

    async def upsert_batch(
        self,
        items: Sequence[tuple[Chunk, list[float], str]],
        *,
        source_id: str,
    ) -> None:
        """Upsert ``(chunk, vector, artifact_hash)`` triples in one batch."""
        ...