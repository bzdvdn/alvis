"""Elasticsearch index — minimal client speaking the Elasticsearch REST API.

Targets Elasticsearch 8.0+'s native ``dense_vector``/``knn`` search
specifically (not OpenSearch's separate k-NN plugin dialect — the two
forked before their vector-search APIs converged, and the request/mapping
shapes differ enough that one client can't honestly serve both). No
``elasticsearch-py`` client dependency, same "hand-roll the REST calls"
approach as :mod:`alvis.sources.s3`'s SigV4 signer and
:mod:`alvis.index.qdrant`.

Unlike ``qdrant``/``pgvector``, this backend has no batch write path yet
(:class:`alvis.index.base.BatchIndexer`) — the engine falls back to its
per-chunk ``upsert`` loop. Elasticsearch's ``_bulk`` endpoint needs a raw
NDJSON request body with its own content type, which the shared
:class:`~alvis.sources.http.HttpClient` (always JSON) doesn't support;
adding that is a reasonable follow-up, not a correctness requirement for a
first version of this backend.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from alvis.core.ids import point_id
from alvis.core.models import Chunk, SearchHit
from alvis.sources.base import SourceError
from alvis.sources.http import HttpClient

_SEARCH_PAGE_SIZE = 200
"""Page size for the ``search_after`` scan :meth:`ElasticsearchIndex.reconcile` runs."""

_KNN_CANDIDATE_FACTOR = 10
_KNN_CANDIDATE_FLOOR = 50
"""``num_candidates`` for the kNN search — ``max(top_k * _KNN_CANDIDATE_FACTOR,
_KNN_CANDIDATE_FLOOR)``, per Elasticsearch's recommendation to over-fetch
candidates for the HNSW graph walk beyond just ``k``."""

#: Reserved field namespace — keys beginning with ``__`` are owned by the
#: engine and drive dedup, incremental skip, and per-source reconcile. User
#: metadata may never use this prefix; the engine rejects such keys on write.
_FIELD_VECTOR = "vector"
_FIELD_TEXT = "__text"
_FIELD_URI = "__uri"
_FIELD_SOURCE = "__source"
_FIELD_HASH = "__hash"
_FIELD_DOCUMENT_ID = "__document_id"
_FIELD_ACL = "__acl"
_FIELD_SCHEMA = "__schema"
_FIELD_SCHEMA_VERSION = 1
_SYSTEM_PREFIX = "__"


def _is_system_key(key: str) -> bool:
    return key.startswith(_SYSTEM_PREFIX)


def _document_identity(chunk: Chunk) -> str:
    """Document-level identity for citations and per-doc rules — see
    :func:`alvis.index.qdrant._document_identity` (same convention)."""
    declared = chunk.metadata.get("documentId")
    if declared is None:
        declared = chunk.metadata.get("document_id")
    return str(declared) if declared else chunk.source_uri


class ElasticsearchIndex:
    """Creates the index on demand and upserts chunk documents.

    Config keys: ``url`` (e.g. http://localhost:9200), ``index`` (the
    Elasticsearch index name), ``api_token_env``/``username`` (Bearer or
    Basic auth, same knobs as every other source/index). Similarity is
    fixed to cosine.

    Document IDs are deterministic (``uuid5`` of source URI + text), so
    re-runs overwrite instead of duplicating; ``reconcile`` prunes stale
    documents.
    """

    def __init__(
        self,
        url: str,
        index: str,
        api_token_env: str | None = None,
        username: str | None = None,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
    ) -> None:
        self.index_name = index
        self.client = HttpClient(
            base_url=url,
            api_token_env=api_token_env,
            username=username,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
        )
        self._dimensions: int | None = None

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
        """Upsert one chunk document, creating the index on demand."""
        self._validate_metadata(chunk.metadata)
        await self._ensure_index(len(vector))
        doc_id = str(point_id(chunk.source_uri, chunk.text))
        await self.client.request(
            "PUT",
            f"/{self.index_name}/_doc/{doc_id}",
            payload=self._document(chunk, vector, source_id, artifact_hash),
            ok_status=(200, 201),
        )

    def _document(
        self, chunk: Chunk, vector: list[float], source_id: str, artifact_hash: str
    ) -> dict[str, object]:
        document: dict[str, object] = {
            _FIELD_VECTOR: vector,
            _FIELD_TEXT: chunk.text,
            _FIELD_URI: chunk.source_uri,
            _FIELD_SOURCE: source_id,
            _FIELD_HASH: artifact_hash,
            _FIELD_DOCUMENT_ID: _document_identity(chunk),
            _FIELD_SCHEMA: _FIELD_SCHEMA_VERSION,
            **chunk.metadata,
        }
        acl = chunk.metadata.get("acl")
        if acl:
            document[_FIELD_ACL] = [str(principal) for principal in acl]
        return document

    @staticmethod
    def _validate_metadata(metadata: Mapping[str, object]) -> None:
        """Reject metadata keys that would collide with the ``__`` namespace."""
        reserved = sorted(key for key in metadata if _is_system_key(key))
        if reserved:
            raise ValueError(
                f"metadata keys starting with {_SYSTEM_PREFIX!r} are reserved for "
                f"the engine: {reserved}"
            )

    async def _ensure_index(self, dimensions: int) -> None:
        if self._dimensions is not None:
            if self._dimensions != dimensions:
                raise ValueError(
                    f"elasticsearch index {self.index_name!r} is {self._dimensions}-"
                    f"dimensional, but a {dimensions}-dimensional vector arrived"
                )
            return
        if await self._index_exists():
            self._dimensions = dimensions
            return
        try:
            await self.client.request(
                "PUT",
                f"/{self.index_name}",
                payload={
                    "mappings": {
                        "properties": {
                            _FIELD_VECTOR: {
                                "type": "dense_vector",
                                "dims": dimensions,
                                "index": True,
                                "similarity": "cosine",
                            },
                            _FIELD_URI: {"type": "keyword"},
                            _FIELD_SOURCE: {"type": "keyword"},
                            _FIELD_HASH: {"type": "keyword"},
                            _FIELD_DOCUMENT_ID: {"type": "keyword"},
                            _FIELD_ACL: {"type": "keyword"},
                        }
                    }
                },
            )
        except SourceError as exc:
            # Unlike qdrant's collection PUT (idempotent — recreating with the
            # same config just succeeds again), Elasticsearch's index-create
            # PUT is not: it 400s if the index already exists. HttpClient's
            # retry-on-timeout assumes every request is safe to repeat: a slow
            # first PUT that actually succeeded server-side but timed out
            # client-side gets retried, and that retry legitimately 400s. Not
            # a real failure — the index is there either way — so treat a 400
            # here as "someone (possibly us, moments ago) already created it"
            # and confirm rather than propagating a false failure.
            if exc.status_code == 400 and await self._index_exists():
                self._dimensions = dimensions
                return
            raise
        self._dimensions = dimensions

    async def _index_exists(self) -> bool:
        try:
            await self.client.request("GET", f"/{self.index_name}")
            return True
        except SourceError as exc:
            if exc.status_code == 404:
                return False
            raise

    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """Nearest-neighbour search via Elasticsearch's ``knn`` search clause.

        The index must already exist (created by an ingestion run).
        ``filters``/``principals`` become a ``bool`` filter nested inside
        the ``knn`` clause's own ``filter`` (applied before the HNSW walk,
        server-side) — same semantics as :meth:`alvis.index.qdrant.
        QdrantIndex.search`: a document with no ``__acl`` (public) or an
        ``__acl`` overlapping ``principals`` is visible; one that has an
        ``__acl`` with no match is excluded outright.
        """
        knn: dict[str, object] = {
            "field": _FIELD_VECTOR,
            "query_vector": vector,
            "k": top_k,
            "num_candidates": max(top_k * _KNN_CANDIDATE_FACTOR, _KNN_CANDIDATE_FLOOR),
        }
        query_filter = _bool_filter(filters, principals)
        if query_filter is not None:
            knn["filter"] = query_filter
        payload: dict[str, object] = {
            "knn": knn,
            "size": top_k,
            "_source": {"excludes": [_FIELD_VECTOR]},
        }
        try:
            response = await self.client.request(
                "POST", f"/{self.index_name}/_search", payload=payload
            )
        except SourceError as exc:
            if exc.status_code == 404:
                return []
            raise
        return [_hit(item) for item in response.get("hits", {}).get("hits", [])]

    async def keyword_search(
        self,
        text: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """Full-text ranking via Elasticsearch's native ``match`` query (BM25).

        Unlike ``qdrant`` (no BM25 endpoint, local scoring over a
        server-narrowed pool), Elasticsearch's own relevance ranking runs
        entirely server-side — this is what the engine was built for.
        ``filters``/``principals`` apply the same server-side clauses as
        :meth:`search`.
        """
        bool_query: dict[str, object] = {"must": [{"match": {_FIELD_TEXT: text}}]}
        query_filter = _bool_filter(filters, principals)
        if query_filter is not None:
            bool_query["filter"] = query_filter
        query: dict[str, object] = {"bool": bool_query}
        try:
            response = await self.client.request(
                "POST",
                f"/{self.index_name}/_search",
                payload={
                    "query": query,
                    "size": top_k,
                    "_source": {"excludes": [_FIELD_VECTOR]},
                },
            )
        except SourceError as exc:
            if exc.status_code == 404:
                return []
            raise
        return [_hit(item) for item in response.get("hits", {}).get("hits", [])]

    async def reconcile(
        self,
        source_id: str,
        current: Mapping[str, str],
    ) -> None:
        """Delete this source's stale documents after scanning them by source id."""
        stale_ids: list[str] = []
        search_after: list[object] | None = None
        while True:
            body: dict[str, object] = {
                "query": {"term": {_FIELD_SOURCE: source_id}},
                "size": _SEARCH_PAGE_SIZE,
                "sort": [{"_id": "asc"}],
                "_source": [_FIELD_URI, _FIELD_HASH],
            }
            if search_after is not None:
                body["search_after"] = search_after
            try:
                response = await self.client.request(
                    "POST", f"/{self.index_name}/_search", payload=body
                )
            except SourceError as exc:
                if exc.status_code == 404:
                    return
                raise
            hits = response.get("hits", {}).get("hits", [])
            if not hits:
                break
            for item in hits:
                source = item.get("_source", {})
                uri = source.get(_FIELD_URI)
                hash_value = source.get(_FIELD_HASH)
                if uri not in current or current[uri] != hash_value:
                    stale_ids.append(item["_id"])
            search_after = hits[-1]["sort"]
            if len(hits) < _SEARCH_PAGE_SIZE:
                break

        for doc_id in stale_ids:
            await self.client.request(
                "DELETE", f"/{self.index_name}/_doc/{doc_id}", ok_status=(200, 404)
            )

    async def count(self) -> int:
        """Total documents in the index (for ``alvis status``)."""
        try:
            response = await self.client.request("GET", f"/{self.index_name}/_count")
        except SourceError as exc:
            if exc.status_code == 404:
                return 0
            raise
        return int(response.get("count", 0))


def _bool_filter(
    filters: Mapping[str, str] | None, principals: Sequence[str] | None
) -> list[dict[str, object]] | None:
    """Filter clauses for a ``bool.filter``/``knn.filter`` array, or ``None``.

    Elasticsearch accepts either one query clause or an array of clauses
    (implicitly ANDed) wherever a filter is expected, so callers plug this
    straight into ``knn.filter`` (:meth:`ElasticsearchIndex.search`) or
    ``bool.filter`` (:meth:`ElasticsearchIndex.keyword_search`) as-is.
    """
    clauses: list[dict[str, object]] = [
        {"term": {key: value}} for key, value in (filters or {}).items()
    ]
    if principals is not None:
        clauses.append(
            {
                "bool": {
                    "should": [
                        {"bool": {"must_not": {"exists": {"field": _FIELD_ACL}}}},
                        {"terms": {_FIELD_ACL: list(principals)}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
    return clauses or None


def _hit(item: dict[str, object]) -> SearchHit:
    source = item.get("_source", {})
    assert isinstance(source, dict)
    raw_score = item.get("_score")
    score = float(raw_score) if isinstance(raw_score, (int, float)) else 0.0
    return SearchHit(
        text=str(source.get(_FIELD_TEXT, "")),
        source_uri=str(source.get(_FIELD_URI, "")),
        metadata={
            key: value
            for key, value in source.items()
            if not _is_system_key(key) and key != _FIELD_VECTOR
        },
        score=score,
    )


__all__ = ["ElasticsearchIndex"]
