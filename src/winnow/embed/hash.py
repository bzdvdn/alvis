"""Embedding stage — chunk → vector."""

from __future__ import annotations

from typing import Protocol

from winnow.core.models import Chunk


class Embedder(Protocol):
    """Maps a chunk to a fixed-size vector."""

    async def embed(self, chunk: Chunk) -> list[float]:
        ...


class HashEmbedder:
    """Deterministic placeholder embedder (type ``default``).

    Produces a fixed-size vector from token n-gram hashes. It is input-check
    placeholder for v0.1 only — swap for a real model in v1.0.
    """

    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions

    async def embed(self, chunk: Chunk) -> list[float]:
        vector = [0.0] * self.dimensions
        import hashlib

        tokens = _tokens(chunk.text)
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[0] % 2 == 0 else -1.0
            vector[index] += sign
        norm = sum(value * value for value in vector) ** 0.5
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


def _tokens(text: str) -> list[str]:
    import re

    return re.findall(r"[a-zа-яё0-9]+", text.lower())


__all__ = ["Embedder", "HashEmbedder"]