"""Qdrant index — minimal client speaking the Qdrant REST API."""

from __future__ import annotations

from collections.abc import Mapping

from alvis.core.ids import point_id
from alvis.core.models import Chunk, SearchHit
from alvis.sources.base import SourceError
from alvis.sources.http import HttpClient

_SCROLL_LIMIT = 100

#: Reserved payload namespace — keys beginning with ``__`` are owned by the
#: engine and drive dedup, incremental skip, and per-source reconcile. User
#: metadata may never use this prefix; the engine rejects such keys on write.
_PAYLOAD_TEXT = "__text"
_PAYLOAD_URI = "__uri"
_PAYLOAD_SOURCE = "__source"
_PAYLOAD_HASH = "__hash"
_PAYLOAD_DOCUMENT_ID = "__document_id"
_PAYLOAD_SCHEMA = "__schema"
_PAYLOAD_SCHEMA_VERSION = 1
_SYSTEM_PREFIX = "__"


def _is_system_key(key: str) -> bool:
    return key.startswith(_SYSTEM_PREFIX)


def _document_identity(chunk: Chunk) -> str:
    """Document-level identity for citations and per-doc rules.

    Sources declare it via ``metadata["documentId"]`` (or its snake_case
    twin) — e.g. a GitLab blob id or Confluence page id, both stable across
    renames. Without a declaration the source URI is used, so existing
    pipelines are unaffected.
    """
    declared = chunk.metadata.get("documentId")
    if declared is None:
        declared = chunk.metadata.get("document_id")
    return str(declared) if declared else chunk.source_uri


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
        """Upsert a chunk point, creating the collection on demand."""
        self._validate_metadata(chunk.metadata)
        await self._ensure_collection(len(vector))
        point = {
            "id": str(point_id(chunk.source_uri, chunk.text)),
            "vector": vector,
            "payload": {
                _PAYLOAD_TEXT: chunk.text,
                _PAYLOAD_URI: chunk.source_uri,
                _PAYLOAD_SOURCE: source_id,
                _PAYLOAD_HASH: artifact_hash,
                _PAYLOAD_DOCUMENT_ID: _document_identity(chunk),
                _PAYLOAD_SCHEMA: _PAYLOAD_SCHEMA_VERSION,
                **chunk.metadata,
            },
        }
        await self.client.request(
            "PUT",
            f"/collections/{self.collection}/points",
            query={"wait": "true"},
            payload={"points": [point]},
        )

    @staticmethod
    def _validate_metadata(metadata: Mapping[str, object]) -> None:
        """Reject metadata keys that would collide with the ``__`` namespace."""
        reserved = sorted(key for key in metadata if _is_system_key(key))
        if reserved:
            raise ValueError(
                f"metadata keys starting with {_SYSTEM_PREFIX!r} are reserved for "
                f"the engine: {reserved}"
            )

    async def reconcile(
        self,
        source_id: str,
        current: Mapping[str, str],
    ) -> None:
        """Delete this source's stale points after scrolling them by source id."""
        filter_payload = {
            "must": [{"key": _PAYLOAD_SOURCE, "match": {"value": source_id}}]
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
                uri = payload.get(_PAYLOAD_URI)
                hash_value = payload.get(_PAYLOAD_HASH)
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
                text=str(item.get("payload", {}).get(_PAYLOAD_TEXT, "")),
                source_uri=str(item.get("payload", {}).get(_PAYLOAD_URI, "")),
                metadata={
                    key: value
                    for key, value in item.get("payload", {}).items()
                    if not _is_system_key(key)
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

    async def count(self) -> int:
        """Total points in the collection (for ``alvis status``)."""
        response = await self.client.request(
            "GET",
            f"/collections/{self.collection}",
        )
        result = response.get("result", {})
        count = result.get("points_count")
        return int(count) if isinstance(count, int) else 0

    async def _collection_exists(self) -> bool:
        try:
            await self.client.request("GET", f"/collections/{self.collection}")
            return True
        except SourceError as exc:
            if exc.status_code == 404:
                return False
            raise