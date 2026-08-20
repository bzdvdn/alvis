"""Deterministic placeholder embedder (type ``default``)."""

from __future__ import annotations

from collections.abc import Sequence

from alvis.core.models import Chunk
from alvis.embed.base import Embedder

__all__ = ["Embedder", "HashEmbedder"]


class HashEmbedder:
    """Deterministic placeholder embedder (type ``default``).

    Produces a fixed-size vector from token n-gram hashes. Deterministic and
    dependency-free; it is a placeholder to run pipelines end-to-end — swap
    for a real model (``openai``) once an embeddings endpoint is available.
    """

    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions
        self.signature = f"default:{dimensions}"

    async def embed(self, chunk: Chunk) -> list[float]:
        """Embed a single chunk (result of :meth:`embed_batch` on ``[chunk]``)."""
        return (await self.embed_batch([chunk]))[0]

    async def embed_batch(self, chunks: Sequence[Chunk]) -> list[list[float]]:
        """Embed chunks deterministically, preserving input order."""
        import hashlib

        vectors: list[list[float]] = []
        for chunk in chunks:
            vector = [0.0] * self.dimensions
            tokens = _tokens(chunk.text)
            for token in tokens:
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "little") % self.dimensions
                sign = 1.0 if digest[0] % 2 == 0 else -1.0
                vector[index] += sign
            norm = sum(value * value for value in vector) ** 0.5
            if norm > 0.0:
                vector = [value / norm for value in vector]
            vectors.append(vector)
        return vectors


def _tokens(text: str) -> list[str]:
    import re

    return re.findall(r"[a-zа-яё0-9]+", text.lower())