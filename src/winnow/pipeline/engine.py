"""Pipeline engine — orchestrates source → artifact → extract → chunk → embed → index."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from winnow.config import ConfigError, PipelineConfig, load_config
from winnow.core.models import Chunk, Document, content_hash
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
from winnow.sources.base import SourceError


@dataclass
class PipelineResult:
    """Summary of a completed pipeline run."""

    documents_ingested: int
    chunks_indexed: int
    embed_cache_hits: int = 0
    embed_cache_misses: int = 0


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

    async def run(self) -> PipelineResult:
        """Execute the configured pipeline: source → extract → chunk → embed → index."""
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

        documents = 0
        indexed = 0
        current: dict[str, str] = {}
        pending: list[tuple[Chunk, str]] = []
        try:
            artifacts = await source.fetch()
        except SourceError as exc:
            raise PipelineError(
                f"source '{self.config.source.type}' failed: {exc}"
            ) from exc

        for artifact in artifacts:
            documents += 1
            artifact_hash = content_hash(artifact)
            current[artifact.uri] = artifact_hash
            try:
                document: Document = await extractor.extract(artifact)
                for chunk in await chunker.chunk(document):
                    indexed += 1
                    if indexer is not None:
                        pending.append((chunk, artifact_hash))
            except (SourceError, ValueError) as exc:
                raise PipelineError(
                    f"failed on artifact {artifact.uri}: {exc}"
                ) from exc

        if indexer is not None and pending:
            chunks = [chunk for chunk, _ in pending]
            try:
                vectors = await embedder.embed_batch(chunks)
            except (SourceError, ValueError) as exc:
                raise PipelineError(f"embedding failed: {exc}") from exc
            finally:
                closer = getattr(embedder, "close", None)
                if callable(closer):
                    closer()
            for (chunk, artifact_hash), vector in zip(pending, vectors, strict=True):
                await indexer.upsert(
                    chunk,
                    vector,
                    source_id=source_id,
                    artifact_hash=artifact_hash,
                )

        if indexer is not None:
            await indexer.reconcile(source_id, current)

        return PipelineResult(
            documents_ingested=documents,
            chunks_indexed=indexed,
            embed_cache_hits=getattr(embedder, "cache_hits", 0),
            embed_cache_misses=getattr(embedder, "cache_misses", 0),
        )