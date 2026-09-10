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
    """Optional capability: lexical (BM25-ish) search alongside vector search.

    Implemented by every built-in backend so ``query(..., hybrid=True)``
    fuses a dense ranking with a keyword ranking (see
    :mod:`alvis.index.fusion`) — but not identically: ``memory``/``sqlite``
    BM25-score the whole corpus locally in Python; ``pgvector`` ranks
    server-side via ``tsvector``/``ts_rank``; ``qdrant`` narrows to a
    candidate pool server-side (full-text payload index) then BM25-scores
    that pool locally, since it has no BM25 endpoint behind this minimal
    client. A backend that implements neither (a plugin without full-text
    support) degrades to vector-only search under ``hybrid=True`` — a
    logged fallback, not an error.
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
    """Optional capability: upsert many chunks in one (or few) round trips.

    Implemented by ``qdrant`` (many points per HTTP PUT) and ``pgvector``
    (a multi-row ``INSERT``, on top of the one connection already reused
    across calls — see :mod:`alvis.index.pgvector`); ``memory``/``sqlite``
    don't need it, already being in-process. The engine falls back to the
    per-chunk ``upsert`` loop when this is absent (a plugin backend, say).
    """

    async def upsert_batch(
        self,
        items: Sequence[tuple[Chunk, list[float], str]],
        *,
        source_id: str,
    ) -> None:
        """Upsert ``(chunk, vector, artifact_hash)`` triples in one batch."""
        ...