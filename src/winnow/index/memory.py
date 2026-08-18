"""In-memory index — for dry-runs, tests, and tiny datasets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from winnow.core.ids import point_id
from winnow.core.models import Chunk, SearchHit


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
        return len(self.points)

    def entries(self) -> list[tuple[Chunk, list[float]]]:
        return [(p.chunk, p.vector) for p in self.points.values()]

    async def search(self, vector: list[float], *, top_k: int = 5) -> list[SearchHit]:
        """Brute-force cosine similarity over in-memory points, best first."""
        points = list(self.points.values())
        points.sort(key=lambda point: _cosine(vector, point.vector), reverse=True)
        return [
            SearchHit(
                text=point.chunk.text,
                source_uri=point.chunk.source_uri,
                metadata=dict(point.chunk.metadata),
                score=_bounded(_cosine(vector, point.vector)),
            )
            for point in points[:top_k]
        ]


def _cosine(left: list[float], right: list[float]) -> float:
    dot: float = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm: float = sum(a * a for a in left) ** 0.5
    right_norm: float = sum(b * b for b in right) ** 0.5
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _bounded(value: float) -> float:
    return max(-1.0, min(1.0, value))