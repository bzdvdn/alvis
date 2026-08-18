"""Qdrant index — minimal client speaking the Qdrant REST API."""

from __future__ import annotations

import uuid

from winnow.core.models import Chunk
from winnow.sources.base import SourceError
from winnow.sources.http import HttpClient


class QdrantIndex:
    """Creates the collection on demand and upserts vector points.

    Config keys: ``url`` (e.g. http://localhost:6333), ``collection``,
    ``api_token_env`` (optional). Distance is fixed to Cosine.
    """

    def __init__(
        self,
        url: str,
        collection: str,
        api_token_env: str | None = None,
    ) -> None:
        self.collection = collection
        self.client = HttpClient(
            base_url=url,
            api_token_env=api_token_env,
        )

    async def upsert(self, chunk: Chunk, vector: list[float]) -> None:
        await self._ensure_collection(len(vector))
        point = {
            "id": str(uuid.uuid4()),
            "vector": vector,
            "payload": {
                "text": chunk.text,
                "source_uri": chunk.source_uri,
                **chunk.metadata,
            },
        }
        await self.client.request(
            "PUT",
            f"/collections/{self.collection}/points",
            query={"wait": "true"},
            payload={"points": [point]},
        )

    async def _ensure_collection(self, dimensions: int) -> None:
        if await self._collection_exists():
            return
        await self.client.request(
            "PUT",
            f"/collections/{self.collection}",
            payload={"vectors": {"size": dimensions, "distance": "Cosine"}},
        )

    async def _collection_exists(self) -> bool:
        try:
            await self.client.request("GET", f"/collections/{self.collection}")
            return True
        except SourceError as exc:
            if exc.status_code == 404:
                return False
            raise