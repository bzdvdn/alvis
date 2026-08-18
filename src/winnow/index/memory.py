"""In-memory index — for dry-runs, tests, and tiny datasets."""

from __future__ import annotations

from dataclasses import dataclass, field

from winnow.core.models import Chunk


@dataclass
class MemoryIndex:
    """Stores (chunk, vector) pairs in memory for the lifetime of the process."""

    entries: list[tuple[Chunk, list[float]]] = field(default_factory=list)

    async def upsert(self, chunk: Chunk, vector: list[float]) -> None:
        self.entries.append((chunk, vector))

    @property
    def count(self) -> int:
        return len(self.entries)