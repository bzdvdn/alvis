"""Qdrant index — minimal client speaking the Qdrant REST API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from alvis.core.ids import point_id
from alvis.core.models import Chunk, SearchHit
from alvis.sources.base import SourceError
from alvis.sources.http import HttpClient

_SCROLL_LIMIT = 100
_UPSERT_BATCH_SIZE = 100
"""Points per PUT in :meth:`QdrantIndex.upsert_batch` — keeps individual
requests bounded regardless of how many chunks a run has pending."""

_KEYWORD_CANDIDATE_FACTOR = 20
_KEYWORD_CANDIDATE_CAP = 500
"""Candidate pool size for :meth:`QdrantIndex.keyword_search` —
``min(max(top_k * _KEYWORD_CANDIDATE_FACTOR, 50), _KEYWORD_CANDIDATE_CAP)``.
Qdrant has no BM25 endpoint behind this minimal REST client (that needs
named sparse vectors, a bigger schema change); the full-text payload index
narrows to a candidate pool server-side, then BM25 scores that pool
locally — the pool must be wide enough for BM25 to have something to rank."""

#: Reserved payload namespace — keys beginning with ``__`` are owned by the
#: engine and drive dedup, incremental skip, and per-source reconcile. User
#: metadata may never use this prefix; the engine rejects such keys on write.
_PAYLOAD_TEXT = "__text"
_PAYLOAD_URI = "__uri"
_PAYLOAD_SOURCE = "__source"
_PAYLOAD_HASH = "__hash"
_PAYLOAD_DOCUMENT_ID = "__document_id"
_PAYLOAD_ACL = "__acl"
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

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self.client.aclose()

    async def upsert(
        self,
        chunk: Chunk,
        vector: list[float],
        *,
        source_id: str,
        artifact_hash: str,
    ) -> None:
        """Upsert a chunk point, creating the collection on demand."""
        await self.upsert_batch([(chunk, vector, artifact_hash)], source_id=source_id)

    async def upsert_batch(
        self,
        items: Sequence[tuple[Chunk, list[float], str]],
        *,
        source_id: str,
    ) -> None:
        """Upsert many chunk points in as few requests as possible.

        One HTTP round trip per :data:`_UPSERT_BATCH_SIZE` points instead of
        one per point — the per-chunk ``upsert`` loop the engine would
        otherwise run pays a full request for every single chunk.
        """
        if not items:
            return
        for chunk, _, _ in items:
            self._validate_metadata(chunk.metadata)
        await self._ensure_collection(len(items[0][1]))
        points = [
            self._point(chunk, vector, source_id, artifact_hash)
            for chunk, vector, artifact_hash in items
        ]
        for start in range(0, len(points), _UPSERT_BATCH_SIZE):
            batch = points[start : start + _UPSERT_BATCH_SIZE]
            await self.client.request(
                "PUT",
                f"/collections/{self.collection}/points",
                query={"wait": "true"},
                payload={"points": batch},
            )

    @staticmethod
    def _point(
        chunk: Chunk, vector: list[float], source_id: str, artifact_hash: str
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            _PAYLOAD_TEXT: chunk.text,
            _PAYLOAD_URI: chunk.source_uri,
            _PAYLOAD_SOURCE: source_id,
            _PAYLOAD_HASH: artifact_hash,
            _PAYLOAD_DOCUMENT_ID: _document_identity(chunk),
            _PAYLOAD_SCHEMA: _PAYLOAD_SCHEMA_VERSION,
            **chunk.metadata,
        }
        acl = chunk.metadata.get("acl")
        if acl:
            payload[_PAYLOAD_ACL] = [str(principal) for principal in acl]
        return {
            "id": str(point_id(chunk.source_uri, chunk.text)),
            "vector": vector,
            "payload": payload,
        }

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

    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """Nearest-neighbour search; scores are cosine similarities.

        The collection must already exist (created by an ingestion run).
        ``filters`` is translated into a Qdrant ``must`` match filter, applied
        server-side before ranking. ``principals``, when given, additionally
        requires a point to have no ``__acl`` (public) or an ``__acl`` that
        overlaps ``principals`` — evaluated server-side too.
        """
        payload: dict[str, object] = {
            "vector": vector,
            "limit": top_k,
            "with_payload": True,
        }
        must: list[dict[str, object]] = [
            {"key": key, "match": {"value": value}} for key, value in (filters or {}).items()
        ]
        if principals is not None:
            must.append(
                {
                    "should": [
                        {"is_empty": {"key": _PAYLOAD_ACL}},
                        {"key": _PAYLOAD_ACL, "match": {"any": list(principals)}},
                    ]
                }
            )
        if must:
            payload["filter"] = {"must": must}
        response = await self.client.request(
            "POST",
            f"/collections/{self.collection}/points/search",
            payload=payload,
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

    async def keyword_search(
        self,
        text: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """BM25-rank a candidate set pulled via Qdrant's full-text payload index.

        Qdrant has no BM25 endpoint behind this minimal REST client — that
        needs named sparse vectors, a bigger schema/ingest change. This
        instead pulls a candidate pool whose ``__text`` matches any query
        token (Qdrant's full-text payload index, server-side ``should``
        match), then BM25-scores that pool locally with
        :mod:`alvis.index.keyword` — same scoring as ``memory``/``sqlite``,
        just sourced from a server-narrowed candidate pool instead of the
        whole corpus. ``filters``/``principals`` apply the same server-side
        clauses as :meth:`search`.
        """
        from alvis.index.keyword import bm25_scores, tokenize

        query_tokens = tokenize(text)
        if not query_tokens:
            return []
        await self._ensure_text_index()
        must: list[dict[str, object]] = [
            {"key": key, "match": {"value": value}} for key, value in (filters or {}).items()
        ]
        if principals is not None:
            must.append(
                {
                    "should": [
                        {"is_empty": {"key": _PAYLOAD_ACL}},
                        {"key": _PAYLOAD_ACL, "match": {"any": list(principals)}},
                    ]
                }
            )
        filter_payload: dict[str, object] = {
            "should": [
                {"key": _PAYLOAD_TEXT, "match": {"text": token}}
                for token in dict.fromkeys(query_tokens)
            ]
        }
        if must:
            filter_payload["must"] = must
        limit = min(max(top_k * _KEYWORD_CANDIDATE_FACTOR, 50), _KEYWORD_CANDIDATE_CAP)
        try:
            response = await self.client.request(
                "POST",
                f"/collections/{self.collection}/points/scroll",
                payload={"filter": filter_payload, "limit": limit, "with_payload": True},
            )
        except SourceError as exc:
            if exc.status_code == 404:
                return []
            raise
        points = response.get("result", {}).get("points", [])
        if not points:
            return []
        documents = [
            tokenize(str(point.get("payload", {}).get(_PAYLOAD_TEXT, ""))) for point in points
        ]
        scores = bm25_scores(query_tokens, documents)
        ranked = sorted(
            zip(points, scores, strict=True), key=lambda pair: pair[1], reverse=True
        )
        return [
            SearchHit(
                text=str(point.get("payload", {}).get(_PAYLOAD_TEXT, "")),
                source_uri=str(point.get("payload", {}).get(_PAYLOAD_URI, "")),
                metadata={
                    key: value
                    for key, value in point.get("payload", {}).items()
                    if not _is_system_key(key)
                },
                score=float(score),
            )
            for point, score in ranked[:top_k]
        ]

    async def _ensure_text_index(self) -> None:
        """Create (or confirm) a full-text payload index on ``__text``.

        Idempotent: Qdrant accepts re-creating an existing payload index
        (it's a no-op replace), so no existence check is needed first.
        """
        try:
            await self.client.request(
                "PUT",
                f"/collections/{self.collection}/index",
                payload={
                    "field_name": _PAYLOAD_TEXT,
                    "field_schema": {
                        "type": "text",
                        "tokenizer": "word",
                        "lowercase": True,
                    },
                },
            )
        except SourceError as exc:
            if exc.status_code == 404:
                return
            raise

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