"""Pipeline engine — orchestrates source → artifact → extract → chunk → embed → index."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from winnow.config import ConfigError, PipelineConfig, load_config
from winnow.core.models import Document, content_hash
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


class PipelineEngine:
    """Executes a pipeline defined declaratively in YAML."""

    def __init__(self, config: PipelineConfig, *, indexer: Indexer | None = None) -> None:
        self.config = config
        self._indexer = indexer

    @classmethod
    def from_yaml(cls, path: str | Path) -> PipelineEngine:
        return cls(load_config(path))

    def describe(self) -> str:
        """Human-readable summary of the configured pipeline (dry-run output)."""
        index = self.config.index.type if self.config.index else "not configured"
        return (
            f"  source: {self.config.source.type}\n"
            f"  extract: {self.config.extract.strategy}\n"
            f"  chunk: {self.config.chunk.strategy} "
            f"(max_tokens={self.config.chunk.max_tokens}, "
            f"overlap={self.config.chunk.overlap})\n"
            f"  embed: {self.config.embed.type}\n"
            f"  index: {index}"
        )

    async def run(self) -> PipelineResult:
        problems = check_pipeline_supported(
            source=self.config.source.type,
            extract=self.config.extract.strategy,
            chunk=self.config.chunk.strategy,
            embed=self.config.embed.type,
            index=self.config.index.type if self.config.index else None,
        )
        if problems:
            raise ConfigError("\n".join(f"  {p}" for p in problems))

        source = build_source(self.config.source)
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
                    if indexer is None:
                        continue
                    vector = await embedder.embed(chunk)
                    await indexer.upsert(
                        chunk,
                        vector,
                        source_id=source_id,
                        artifact_hash=artifact_hash,
                    )
            except (SourceError, ValueError) as exc:
                raise PipelineError(
                    f"failed on artifact {artifact.uri}: {exc}"
                ) from exc

        if indexer is not None:
            await indexer.reconcile(source_id, current)

        return PipelineResult(documents_ingested=documents, chunks_indexed=indexed)