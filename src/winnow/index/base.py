"""Indexer protocol."""

from __future__ import annotations

from typing import Protocol

from winnow.core.models import Chunk


class Indexer(Protocol):
    """Persists chunk vectors into a store."""

    async def upsert(self, chunk: Chunk, vector: list[float]) -> None:
        ...