"""Pipeline engine — orchestrates source → artifact → extract → chunk → embed → index."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from winnow.config import ConfigError, PipelineConfig, load_config
from winnow.core.models import Document
from winnow.factories import (
    build_chunker,
    build_embedder,
    build_extractor,
    build_indexer,
    build_source,
)
from winnow.registry import check_pipeline_supported


@dataclass
class PipelineResult:
    """Summary of a completed pipeline run."""

    documents_ingested: int
    chunks_indexed: int


class PipelineEngine:
    """Executes a pipeline defined declaratively in YAML."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    @classmethod
    def from_yaml(cls, path: str | Path) -> PipelineEngine:
        return cls(load_config(path))

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
        indexer = build_indexer(self.config.index) if self.config.index else None

        documents = 0
        indexed = 0
        for artifact in await source.fetch():
            documents += 1
            document: Document = await extractor.extract(artifact)
            for chunk in await chunker.chunk(document):
                indexed += 1
                if indexer is None:
                    continue
                vector = await embedder.embed(chunk)
                await indexer.upsert(chunk, vector)
        return PipelineResult(documents_ingested=documents, chunks_indexed=indexed)