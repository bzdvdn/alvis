"""Indexer protocol."""

from __future__ import annotations

from collections.abc import Mapping
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
    ) -> list[SearchHit]:
        """Return the ``top_k`` closest chunks to ``vector``, best first.

        Scores are cosine similarities in ``[0, 1]``; implementations must
        sort results so the highest score comes first.
        """
        ...