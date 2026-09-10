"""OpenAI-compatible embedding API adapter (``openai`` embed type).

Calls ``POST {base_url}/embeddings`` with ``{"model": ..., "input": [...]}``
and expects OpenAI's response shape (``data[].embedding``). Works with
OpenAI, Azure OpenAI, local self-hosted servers (vLLM, text-embeddings-
inference, Ollama-compatible gateways), and any service exposing the same
contract. Inputs are sent in batches with the same retry/backoff semantics
as sources.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import httpx

from alvis.core.models import Chunk
from alvis.embed.hash import HashEmbedder
from alvis.sources.http import HttpClient


class ApiEmbedder:
    """Embeds chunks via a remote embeddings endpoint (type ``openai``).

    ``max_concurrency`` bounds how many ``/embeddings`` requests are in
    flight at once when a run has more than one ``batch_size`` worth of
    chunks pending — a large corpus's batches are dispatched concurrently
    (default 4 in flight) rather than one at a time, without unbounded
    fan-out that could trip the provider's rate limit despite the
    per-request retry/backoff. Set to ``1`` for the old fully-sequential
    behavior.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_token_env: str | None = None,
        batch_size: int = 32,
        max_concurrency: int = 4,
        timeout: float = 60.0,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.model = model
        self.batch_size = batch_size
        self.max_concurrency = max_concurrency
        self.client = HttpClient(
            base_url=base_url,
            api_token_env=api_token_env,
            timeout=timeout,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
            transport=transport,
        )
        self.signature = f"openai:{model}"

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self.client.aclose()

    async def embed(self, chunk: Chunk) -> list[float]:
        """Embed a single chunk (result of :meth:`embed_batch` on ``[chunk]``)."""
        return (await self.embed_batch([chunk]))[0]

    async def embed_batch(self, chunks: Sequence[Chunk]) -> list[list[float]]:
        """Embed chunks in batches, preserving input order.

        Batches are requested concurrently, bounded by ``max_concurrency``
        (see class docstring); results are reassembled in the original
        batch order regardless of completion order.
        """
        batches = [
            chunks[start : start + self.batch_size]
            for start in range(0, len(chunks), self.batch_size)
        ]
        if not batches:
            return []
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _embed_one(batch: Sequence[Chunk]) -> list[list[float]]:
            async with semaphore:
                response = await self.client.request(
                    "POST",
                    "/embeddings",
                    payload={"model": self.model, "input": [chunk.text for chunk in batch]},
                    ok_status=(200,),
                )
            entries = sorted(response["data"], key=lambda item: item["index"])
            return [entry["embedding"] for entry in entries]

        results = await asyncio.gather(*(_embed_one(batch) for batch in batches))
        vectors: list[list[float]] = []
        for result in results:
            vectors.extend(result)
        return vectors


__all__ = ["ApiEmbedder", "HashEmbedder"]