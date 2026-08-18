"""OpenAI-compatible embedding API adapter (``openai`` embed type).

Calls ``POST {base_url}/embeddings`` with ``{"model": ..., "input": [...]}``
and expects OpenAI's response shape (``data[].embedding``). Works with
OpenAI, Azure OpenAI, local self-hosted servers (vLLM, text-embeddings-
inference, Ollama-compatible gateways), and any service exposing the same
contract. Inputs are sent in batches with the same retry/backoff semantics
as sources.
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from winnow.core.models import Chunk
from winnow.embed.hash import HashEmbedder
from winnow.sources.http import HttpClient


class ApiEmbedder:
    """Embeds chunks via a remote embeddings endpoint (type ``openai``)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_token_env: str | None = None,
        batch_size: int = 32,
        timeout: float = 60.0,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.batch_size = batch_size
        self.client = HttpClient(
            base_url=base_url,
            api_token_env=api_token_env,
            timeout=timeout,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
            transport=transport,
        )

    async def embed(self, chunk: Chunk) -> list[float]:
        """Embed a single chunk (result of :meth:`embed_batch` on ``[chunk]``)."""
        return (await self.embed_batch([chunk]))[0]

    async def embed_batch(self, chunks: Sequence[Chunk]) -> list[list[float]]:
        """Embed chunks in batches, preserving input order."""
        vectors: list[list[float]] = []
        for start in range(0, len(chunks), self.batch_size):
            batch = chunks[start : start + self.batch_size]
            response = await self.client.request(
                "POST",
                "/embeddings",
                payload={"model": self.model, "input": [chunk.text for chunk in batch]},
                ok_status=(200,),
            )
            entries = sorted(
                response["data"], key=lambda item: item["index"]
            )
            vectors.extend(entry["embedding"] for entry in entries)
        return vectors


__all__ = ["ApiEmbedder", "HashEmbedder"]