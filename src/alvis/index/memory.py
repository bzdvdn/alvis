"""In-memory index — for dry-runs, tests, and tiny datasets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from alvis.core.ids import point_id
from alvis.core.models import Chunk, SearchHit
from alvis.index._acl import acl_visible
from alvis.index._math import clamp, cosine_similarity
from alvis.index.keyword import bm25_scores, tokenize


def _matches(metadata: Mapping[str, object], filters: Mapping[str, str] | None) -> bool:
    if not filters:
        return True
    return all(str(metadata.get(key)) == value for key, value in filters.items())


@dataclass
class _Point:
    source_id: str
    artifact_hash: str
    chunk: Chunk
    vector: list[float]


@dataclass
class MemoryIndex:
    """Stores (chunk, vector) pairs in memory for the lifetime of the process.

    Point IDs are deterministic (see ``point_id``), so repeated upserts of the
    same content overwrite instead of accumulating.
    """

    points: dict[str, _Point] = field(default_factory=dict)

    async def upsert(
        self,
        chunk: Chunk,
        vector: list[float],
        *,
        source_id: str,
        artifact_hash: str,
    ) -> None:
        """Store a chunk under its deterministic point ID (overwrites on repeat)."""
        pid = str(point_id(chunk.source_uri, chunk.text))
        self.points[pid] = _Point(
            source_id=source_id,
            artifact_hash=artifact_hash,
            chunk=chunk,
            vector=vector,
        )

    async def reconcile(
        self,
        source_id: str,
        current: Mapping[str, str],
    ) -> None:
        """Prune this source's points whose uri/artifact_hash are no longer current."""
        stale = [
            pid
            for pid, point in self.points.items()
            if point.source_id == source_id
            and (
                point.chunk.source_uri not in current
                or current[point.chunk.source_uri] != point.artifact_hash
            )
        ]
        for pid in stale:
            del self.points[pid]

    @property
    def count(self) -> int:
        """Number of stored points."""
        return len(self.points)

    def entries(self) -> list[tuple[Chunk, list[float]]]:
        """Return stored ``(chunk, vector)`` pairs."""
        return [(p.chunk, p.vector) for p in self.points.values()]

    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """Brute-force cosine similarity over in-memory points, best first.

        ``filters`` keeps only points whose metadata matches every pair.
        ``principals`` additionally drops points whose ``acl`` metadata
        doesn't include any of them (see :class:`alvis.index.base.Indexer`).
        """
        points = [
            p
            for p in self.points.values()
            if _matches(p.chunk.metadata, filters)
            and acl_visible(p.chunk.metadata, principals)
        ]
        points.sort(key=lambda point: cosine_similarity(vector, point.vector), reverse=True)
        return [
            SearchHit(
                text=point.chunk.text,
                source_uri=point.chunk.source_uri,
                metadata=dict(point.chunk.metadata),
                score=clamp(cosine_similarity(vector, point.vector)),
            )
            for point in points[:top_k]
        ]

    async def keyword_search(
        self,
        text: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """BM25 keyword search over in-memory points, best first."""
        points = [
            p
            for p in self.points.values()
            if _matches(p.chunk.metadata, filters)
            and acl_visible(p.chunk.metadata, principals)
        ]
        query_tokens = tokenize(text)
        scores = bm25_scores(query_tokens, [tokenize(p.chunk.text) for p in points])
        ranked = sorted(zip(points, scores, strict=True), key=lambda item: item[1], reverse=True)
        return [
            SearchHit(
                text=point.chunk.text,
                source_uri=point.chunk.source_uri,
                metadata=dict(point.chunk.metadata),
                score=score,
            )
            for point, score in ranked[:top_k]
        ]