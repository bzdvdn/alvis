"""Pipeline engine — orchestrates source → artifact → extract → chunk → embed → index."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from winnow import observability as ob
from winnow.config import ConfigError, PipelineConfig, load_config
from winnow.core.models import Chunk, Document, DocumentMeta, content_hash
from winnow.docstore import DocEntry, DocStore
from winnow.errors import PipelineError
from winnow.factories import (
    build_chunker,
    build_embedder,
    build_extractor,
    build_indexer,
    build_source,
    source_identity,
)
from winnow.index.base import Indexer
from winnow.registry import check_pipeline_supported
from winnow.sources.base import ListingSource, SourceError


@dataclass
class PipelineResult:
    """Summary of a completed pipeline run.

    ``documents_ingested`` counts every document the source reported;
    ``documents_changed``/``documents_skipped``/``documents_deleted`` break
    it into what incremental ingestion actually did.
    """

    documents_ingested: int
    chunks_indexed: int
    embed_cache_hits: int = 0
    embed_cache_misses: int = 0
    documents_changed: int = 0
    documents_skipped: int = 0
    documents_deleted: int = 0


def pipeline_signature(config: PipelineConfig) -> str:
    """Fingerprints the transformation stages, not the content.

    Incremental state is stored per (source, signature): the moment extract /
    chunk / embed settings change, stored fingerprints are treated as stale
    and the whole source is reprocessed — never silently left un-re-chunked.
    """
    payload: dict[str, object] = {
        "extract": config.extract.strategy,
        "chunk": {
            "strategy": config.chunk.strategy,
            "max_tokens": config.chunk.max_tokens,
            "overlap": config.chunk.overlap,
            "max_chars": config.chunk.max_chars,
            "overlap_chars": config.chunk.overlap_chars,
        },
        "embed_type": config.embed.type,
        "embed_model": config.embed.config.get("model"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


class PipelineEngine:
    """Executes a pipeline defined declaratively in YAML."""

    def __init__(self, config: PipelineConfig, *, indexer: Indexer | None = None) -> None:
        self.config = config
        self._indexer = indexer

    @classmethod
    def from_yaml(cls, path: str | Path) -> PipelineEngine:
        """Build an engine from a pipeline YAML config file."""
        return cls(load_config(path))

    def describe(self) -> str:
        """Human-readable summary of the configured pipeline (dry-run output)."""
        index = self.config.index.type if self.config.index else "not configured"
        return (
            f"  source: {self._source_summary()}\n"
            f"  extract: {self.config.extract.strategy}\n"
            f"  chunk: {self._chunk_summary()}\n"
            f"  embed: {self._embed_summary()}\n"
            f"  index: {index}"
        )

    def describe_graph(self) -> str:
        """ASCII flowchart of the configured pipeline stages."""
        edges = "\n".join(("  │", "  ▼"))
        index = self.config.index.type if self.config.index else "not configured"
        return "\n".join(
            [
                f"  source {self._source_summary()}",
                *edges.split("\n"),
                f"  extract {self.config.extract.strategy}",
                *edges.split("\n"),
                f"  chunk {self._chunk_summary()}",
                *edges.split("\n"),
                f"  embed {self._embed_summary()}",
                *edges.split("\n"),
                f"  index {index}",
            ]
        )

    def _source_summary(self) -> str:
        source = self.config.source
        key = {
            "fs": "path",
            "confluence": "space",
            "github": "repo",
            "gitlab": "project",
            "s3": "bucket",
        }.get(source.type)
        value = source.config.get(key) if key else None
        if value is not None:
            return f"{source.type} ({key}={value})"
        return source.type

    def _chunk_summary(self) -> str:
        if self.config.chunk.strategy == "size":
            return (
                f"size (max_chars={self.config.chunk.max_chars}, "
                f"overlap_chars={self.config.chunk.overlap_chars})"
            )
        return (
            f"{self.config.chunk.strategy} "
            f"(max_tokens={self.config.chunk.max_tokens}, "
            f"overlap={self.config.chunk.overlap})"
        )

    def _embed_summary(self) -> str:
        model = self.config.embed.config.get("model")
        if model is not None:
            return f"{self.config.embed.type}:{model}"
        return self.config.embed.type

    def _index_summary(self) -> str:
        if self.config.index is None:
            return "not configured"
        index = self.config.index
        key = {
            "qdrant": "collection",
            "pgvector": "table",
        }.get(index.type)
        value = index.config.get(key) if key else None
        if value is not None:
            return f"{index.type} ({key}={value})"
        return index.type

    @property
    def _metric_labels(self) -> dict[str, str]:
        index = self.config.index.type if self.config.index else "none"
        return {"source": self.config.source.type, "index": index}

    async def run(self, *, docstore: DocStore | None = None) -> PipelineResult:
        """Execute the configured pipeline: source → extract → chunk → embed → index.

        With a ``docstore``, unchanged documents are skipped. When the source
        supports cheap listing (``list_documents``), their bodies are not even
        downloaded; otherwise bodies are fetched and compared by content hash.
        State is committed only after the run succeeds.

        Observability: every run updates the process-wide metrics
        (``pipeline_runs_total``, ``pipeline_duration_seconds``, resource
        counters, ``pipeline_failures_total`` on error) and emits a
        ``pipeline.completed`` / ``pipeline.failed`` structured log line.
        """
        started = time.monotonic()
        async with ob.span("pipeline.run", attributes=self._metric_labels):
            try:
                result = await self._execute(docstore)
            except Exception:
                ob.inc("pipeline_failures_total", labels=self._metric_labels)
                ob.LOG.exception("pipeline.failed", extra=self._metric_labels)
                raise
        seconds = time.monotonic() - started
        ob.inc("pipeline_runs_total", labels=self._metric_labels)
        ob.inc(
            "pipeline_documents_total",
            amount=result.documents_ingested,
            labels=self._metric_labels,
        )
        ob.inc(
            "pipeline_chunks_total",
            amount=result.chunks_indexed,
            labels=self._metric_labels,
        )
        ob.inc(
            "pipeline_changed_total",
            amount=result.documents_changed,
            labels=self._metric_labels,
        )
        ob.inc(
            "pipeline_skipped_total",
            amount=result.documents_skipped,
            labels=self._metric_labels,
        )
        ob.inc(
            "pipeline_deleted_total",
            amount=result.documents_deleted,
            labels=self._metric_labels,
        )
        ob.observe("pipeline_duration_seconds", value=seconds, labels=self._metric_labels)
        ob.LOG.info(
            "pipeline.completed",
            extra={
                **self._metric_labels,
                "duration_s": round(seconds, 4),
                "documents": result.documents_ingested,
                "chunks": result.chunks_indexed,
                "changed": result.documents_changed,
                "skipped": result.documents_skipped,
                "deleted": result.documents_deleted,
                "embed_cache_hits": result.embed_cache_hits,
                "embed_cache_misses": result.embed_cache_misses,
            },
        )
        return result

    async def _execute(self, docstore: DocStore | None = None) -> PipelineResult:
        """Run the stages without the observability wrapper (see :meth:`run`)."""
        problems = check_pipeline_supported(
            source=self.config.source.type,
            extract=self.config.extract.strategy,
            chunk=self.config.chunk.strategy,
            embed=self.config.embed.type,
            index=self.config.index.type if self.config.index else None,
        )
        if problems:
            raise ConfigError("\n".join(f"  {p}" for p in problems))

        source = build_source(
            self.config.source,
            max_bytes=self.config.extract.max_bytes,
        )
        extractor = build_extractor(self.config.extract)
        chunker = build_chunker(self.config.chunk)
        embedder = build_embedder(self.config.embed)
        indexer = self._indexer or (
            build_indexer(self.config.index) if self.config.index else None
        )
        source_id = source_identity(self.config.source)
        signature = pipeline_signature(self.config) if docstore is not None else None
        list_documents = getattr(source, "list_documents", None)
        use_listing = docstore is not None and list_documents is not None

        documents = 0
        indexed = 0
        changed = 0
        skipped = 0
        deleted = 0
        current: dict[str, str] = {}
        entries: dict[str, DocEntry] = {}
        pending: list[tuple[Chunk, str]] = []
        use_listing = False
        uri_by_meta: dict[str, DocumentMeta] = {}

        try:
            if docstore is not None and signature is not None and list_documents is not None:
                use_listing = True
                listing_source = cast(ListingSource, source)
                with ob.timer(
                    "pipeline_stage_seconds",
                    labels={**self._metric_labels, "stage": "list"},
                ):
                    metas = await listing_source.list_documents()
                uri_by_meta = {meta.uri: meta for meta in metas}
                wanted: set[str] = set()
                for meta in metas:
                    documents += 1
                    stored = docstore.entry(source_id, signature, meta.uri)
                    if (
                        stored is not None
                        and stored.listing is not None
                        and stored.listing == meta.fingerprint
                    ):
                        skipped += 1
                        current[meta.uri] = stored.content
                        entries[meta.uri] = stored
                    else:
                        changed += 1
                        wanted.add(meta.uri)
                with ob.timer(
                    "pipeline_stage_seconds",
                    labels={**self._metric_labels, "stage": "fetch"},
                ):
                    artifacts = await listing_source.fetch(uris=wanted) if wanted else []
            else:
                with ob.timer(
                    "pipeline_stage_seconds",
                    labels={**self._metric_labels, "stage": "fetch"},
                ):
                    artifacts = await source.fetch()
        except SourceError as exc:
            raise PipelineError(
                f"source '{self.config.source.type}' failed: {exc}"
            ) from exc

        with ob.timer(
            "pipeline_stage_seconds",
            labels={**self._metric_labels, "stage": "extract"},
        ):
            for artifact in artifacts:
                artifact_hash = content_hash(artifact)
                current[artifact.uri] = artifact_hash
                if not use_listing:
                    documents += 1
                    stored = (
                        docstore.entry(source_id, signature, artifact.uri)
                        if docstore is not None and signature is not None
                        else None
                    )
                    if stored is not None and stored.content == artifact_hash:
                        skipped += 1
                        entries[artifact.uri] = DocEntry(content=artifact_hash)
                        continue
                    changed += 1
                listed = uri_by_meta.get(artifact.uri)
                entries[artifact.uri] = DocEntry(
                    content=artifact_hash,
                    listing=listed.fingerprint if listed is not None else None,
                )
                try:
                    document: Document = await extractor.extract(artifact)
                    doc_chunks = 0
                    for chunk in await chunker.chunk(document):
                        indexed += 1
                        doc_chunks += 1
                        if indexer is not None:
                            enriched = chunk.model_copy(
                                update={"metadata": {**artifact.metadata, **chunk.metadata}}
                            )
                            pending.append((enriched, artifact_hash))
                    ob.LOG.debug(
                        "artifact.processed",
                        extra={
                            "uri": artifact.uri,
                            "chunks": doc_chunks,
                            "bytes": len(artifact.data),
                        },
                    )
                except (SourceError, ValueError) as exc:
                    raise PipelineError(
                        f"failed on artifact {artifact.uri}: {exc}"
                    ) from exc

        if indexer is not None and pending:
            chunks = [chunk for chunk, _ in pending]
            try:
                with ob.timer(
                    "pipeline_stage_seconds",
                    labels={**self._metric_labels, "stage": "embed"},
                ):
                    vectors = await embedder.embed_batch(chunks)
                ob.LOG.debug(
                    "embed.batch",
                    extra={**self._metric_labels, "chunks": len(chunks)},
                )
            except (SourceError, ValueError) as exc:
                raise PipelineError(f"embedding failed: {exc}") from exc
            finally:
                closer = getattr(embedder, "close", None)
                if callable(closer):
                    closer()
            with ob.timer(
                "pipeline_stage_seconds",
                labels={**self._metric_labels, "stage": "upsert"},
            ):
                for (chunk, artifact_hash), vector in zip(pending, vectors, strict=True):
                    await indexer.upsert(
                        chunk,
                        vector,
                        source_id=source_id,
                        artifact_hash=artifact_hash,
                    )

        if indexer is not None:
            with ob.timer(
                "pipeline_stage_seconds",
                labels={**self._metric_labels, "stage": "reconcile"},
            ):
                await indexer.reconcile(source_id, current)

        if docstore is not None and signature is not None:
            with ob.timer(
                "pipeline_stage_seconds",
                labels={**self._metric_labels, "stage": "commit"},
            ):
                deleted = docstore.commit(source_id, signature, entries)
                docstore.save()

        return PipelineResult(
            documents_ingested=documents,
            chunks_indexed=indexed,
            embed_cache_hits=getattr(embedder, "cache_hits", 0),
            embed_cache_misses=getattr(embedder, "cache_misses", 0),
            documents_changed=changed,
            documents_skipped=skipped,
            documents_deleted=deleted,
        )