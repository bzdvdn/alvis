"""Pipeline engine — orchestrates source → artifact → extract → chunk → embed → index."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import winnow.observability as ob
from winnow.config import ConfigError, PipelineConfig, load_config
from winnow.docstore import DocStore
from winnow.factories import (
    build_chunker,
    build_embedder,
    build_extractor,
    build_indexer,
    build_source,
    source_identity,
)
from winnow.index.base import Indexer
from winnow.pipeline.stages import STAGES, RunContext
from winnow.registry import check_pipeline_supported


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
        """Run the pipeline stages without the observability wrapper (see :meth:`run`).

        Adapters and validation live in :meth:`_build_context`; the stages
        (:mod:`winnow.pipeline.stages`) then share a single :class:`RunContext`
        and run in a fixed order, each timed as its own ``pipeline_stage_seconds
        stage=<name>`` sample.
        """
        ctx = self._build_context(docstore)
        for stage in STAGES:
            if stage.applicable(ctx):
                with ob.timer(
                    "pipeline_stage_seconds",
                    labels=ctx.stage_labels(stage.name),
                ):
                    await stage.run(ctx)
        return PipelineResult(
            documents_ingested=ctx.documents,
            chunks_indexed=ctx.indexed,
            embed_cache_hits=ctx.embed_cache_hits,
            embed_cache_misses=ctx.embed_cache_misses,
            documents_changed=ctx.changed,
            documents_skipped=ctx.skipped,
            documents_deleted=ctx.deleted,
        )

    def _build_context(self, docstore: DocStore | None) -> RunContext:
        """Validate the config and wire the adapters into a fresh :class:`RunContext`."""
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
        signature = pipeline_signature(self.config) if docstore is not None else None
        return RunContext(
            config=self.config,
            docstore=docstore,
            source_id=source_identity(self.config.source),
            signature=signature,
            source=source,
            extractor=extractor,
            chunker=chunker,
            embedder=embedder,
            indexer=indexer,
            metric_labels=self._metric_labels,
        )