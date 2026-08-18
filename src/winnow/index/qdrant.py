"""Qdrant index — minimal client speaking the Qdrant REST API."""

from __future__ import annotations

from collections.abc import Mapping

from winnow.core.ids import point_id
from winnow.core.models import Chunk, SearchHit
from winnow.sources.base import SourceError
from winnow.sources.http import HttpClient

_SCROLL_LIMIT = 100


class QdrantIndex:
    """Creates the collection on demand and upserts vector points.

    Config keys: ``url`` (e.g. http://localhost:6333), ``collection``,
    ``api_token_env`` (optional). Distance is fixed to Cosine.

    Point IDs are deterministic (``uuid5`` of source URI + text), so re-runs
    overwrite instead of duplicating; ``reconcile`` prunes stale points.
    """

    def __init__(
        self,
        url: str,
        collection: str,
        api_token_env: str | None = None,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
    ) -> None:
        self.collection = collection
        self.client = HttpClient(
            base_url=url,
            api_token_env=api_token_env,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
        )

    async def upsert(
        self,
        chunk: Chunk,
        vector: list[float],
        *,
        source_id: str,
        artifact_hash: str,
    ) -> None:
        await self._ensure_collection(len(vector))
        point = {
            "id": str(point_id(chunk.source_uri, chunk.text)),
            "vector": vector,
            "payload": {
                "text": chunk.text,
                "source_uri": chunk.source_uri,
                "_source": source_id,
                "artifact_hash": artifact_hash,
                **chunk.metadata,
            },
        }
        await self.client.request(
            "PUT",
            f"/collections/{self.collection}/points",
            query={"wait": "true"},
            payload={"points": [point]},
        )

    async def reconcile(
        self,
        source_id: str,
        current: Mapping[str, str],
    ) -> None:
        filter_payload = {
            "must": [{"key": "_source", "match": {"value": source_id}}]
        }
        stale_ids: list[str] = []
        offset: str | None = None
        while True:
            scroll: dict[str, object] = {
                "filter": filter_payload,
                "limit": _SCROLL_LIMIT,
                "with_payload": True,
            }
            if offset:
                scroll["offset"] = offset
            page = await self.client.request(
                "POST",
                f"/collections/{self.collection}/points/scroll",
                payload=scroll,
            )
            result = page.get("result", {})
            for point in result.get("points", []):
                payload = point.get("payload", {})
                uri = payload.get("source_uri")
                hash_value = payload.get("artifact_hash")
                if uri not in current or current[uri] != hash_value:
                    stale_ids.append(point["id"])
            offset = result.get("next_page_offset")
            if not offset or not result.get("points"):
                break

        if stale_ids:
            await self.client.request(
                "POST",
                f"/collections/{self.collection}/points/delete",
                payload={"points": stale_ids},
            )

    async def search(self, vector: list[float], *, top_k: int = 5) -> list[SearchHit]:
        """Nearest-neighbour search; scores are cosine similarities.

        The collection must already exist (created by an ingestion run).
        """
        response = await self.client.request(
            "POST",
            f"/collections/{self.collection}/points/search",
            payload={
                "vector": vector,
                "limit": top_k,
                "with_payload": True,
            },
        )
        return [
            SearchHit(
                text=str(item.get("payload", {}).get("text", "")),
                source_uri=str(item.get("payload", {}).get("source_uri", "")),
                metadata={
                    key: value
                    for key, value in item.get("payload", {}).items()
                    if key not in ("text", "source_uri", "_source", "artifact_hash")
                },
                score=float(item.get("score", 0.0)),
            )
            for item in response.get("result", [])
        ]

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