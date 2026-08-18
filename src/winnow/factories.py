"""Build stage adapters from declarative config."""

from __future__ import annotations

from winnow.chunk import AutoChunker
from winnow.config.loader import ConfigError
from winnow.config.models import (
    ChunkConfig,
    EmbedConfig,
    ExtractConfig,
    IndexConfig,
    SourceConfig,
)
from winnow.embed import HashEmbedder
from winnow.extract import AutoExtractor
from winnow.index import MemoryIndex, QdrantIndex
from winnow.registry import KNOWN_SOURCES
from winnow.sources import (
    ConfluenceSource,
    FilesystemSource,
    GitHubSource,
    GitLabSource,
    S3Source,
)


def source_identity(config: SourceConfig) -> str:
    """Stable identifier of a source for point namespacing in the index."""
    if config.type == "fs":
        return f"fs:{config.config.get('path', '')}"
    if config.type == "confluence":
        return f"confluence:{config.config.get('space', '')}@{config.config.get('url', '')}"
    if config.type == "github":
        return f"github:{config.config.get('repo', '')}"
    if config.type == "gitlab":
        host = config.config.get("url", "https://gitlab.com")
        return f"gitlab:{config.config.get('project', '')}@{host}"
    if config.type == "s3":
        return (
            f"s3:{config.config.get('bucket', '')}"
            f"@{config.config.get('url', '')}"
        )
    return config.type


def build_source(
    config: SourceConfig,
) -> FilesystemSource | ConfluenceSource | GitHubSource | GitLabSource | S3Source:
    if config.type == "fs":
        return FilesystemSource(**config.config)
    if config.type == "confluence":
        return ConfluenceSource(**config.config)
    if config.type == "github":
        return GitHubSource(**config.config)
    if config.type == "gitlab":
        return GitLabSource(**config.config)
    if config.type == "s3":
        return S3Source(**config.config)
    raise ConfigError(
        f"source type {config.type!r} is not implemented in v0.1 "
        f"(known: {sorted(KNOWN_SOURCES)})"
    )


def build_extractor(config: ExtractConfig) -> AutoExtractor:
    if config.strategy == "auto":
        return AutoExtractor()
    raise ConfigError(f"extract strategy {config.strategy!r} is not implemented")


def build_chunker(config: ChunkConfig) -> AutoChunker:
    if config.strategy == "auto":
        return AutoChunker(max_tokens=config.max_tokens, overlap=config.overlap)
    raise ConfigError(f"chunk strategy {config.strategy!r} is not implemented")


def build_embedder(config: EmbedConfig) -> HashEmbedder:
    if config.type == "default":
        return HashEmbedder()
    raise ConfigError(f"embed type {config.type!r} is not implemented")


def build_indexer(config: IndexConfig) -> MemoryIndex | QdrantIndex:
    if config.type == "memory":
        return MemoryIndex()
    if config.type == "qdrant":
        return QdrantIndex(**config.config)
    raise ConfigError(f"index type {config.type!r} is not implemented in v0.1")