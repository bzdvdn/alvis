"""Embedder contract — model-agnostic, batch-friendly.

Every embedder implements :meth:`Embedder.embed_batch` (the pipeline only
calls this), plus a single-chunk convenience :meth:`embed`. ``signature``
identifies the exact model+config so caches never mix vectors from
different models.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from winnow.core.models import Chunk


class Embedder(Protocol):
    """Maps chunks to fixed-size vectors."""

    signature: str
    """Stable identity of the model+config (e.g. ``openai:text-embedding``)."""

    async def embed(self, chunk: Chunk) -> list[float]:
        """Embed a single chunk."""
        ...

    async def embed_batch(self, chunks: Sequence[Chunk]) -> list[list[float]]:
        """Embed chunks, preserving input order.

        Implementations may batch network calls internally (e.g. one API
        call per ``batch_size``) but must return one vector per input chunk.
        """
        ...


__all__ = ["Embedder"]