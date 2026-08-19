"""Build stage adapters from declarative config."""

from __future__ import annotations

from winnow.chunk import AutoChunker, SectionsChunker, SizeChunker
from winnow.config.loader import ConfigError
from winnow.config.models import (
    ChunkConfig,
    EmbedConfig,
    ExtractConfig,
    IndexConfig,
    SourceConfig,
)
from winnow.embed import (
    ApiEmbedder,
    CachingEmbedder,
    Embedder,
    FileEmbeddingCache,
    HashEmbedder,
    InMemoryEmbeddingCache,
)
from winnow.extract import AutoExtractor
from winnow.index import MemoryIndex, PgVectorIndex, QdrantIndex
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
    *,
    max_bytes: int | None = None,
) -> FilesystemSource | ConfluenceSource | GitHubSource | GitLabSource | S3Source:
    """Build the source adapter declared by ``config``."""
    if config.type == "fs":
        return FilesystemSource(**config.config, max_bytes=max_bytes)
    if config.type == "confluence":
        return ConfluenceSource(**config.config)
    if config.type == "github":
        return GitHubSource(**config.config, max_bytes=max_bytes)
    if config.type == "gitlab":
        return GitLabSource(**config.config, max_bytes=max_bytes)
    if config.type == "s3":
        return S3Source(**config.config, max_bytes=max_bytes)
    raise ConfigError(
        f"source type {config.type!r} is not implemented in v0.1 "
        f"(known: {sorted(KNOWN_SOURCES)})"
    )


def build_extractor(config: ExtractConfig) -> AutoExtractor:
    """Build the extraction strategy declared by ``config``."""
    if config.strategy == "auto":
        return AutoExtractor()
    raise ConfigError(f"extract strategy {config.strategy!r} is not implemented")


def build_chunker(config: ChunkConfig) -> AutoChunker | SectionsChunker | SizeChunker:
    """Build the chunking strategy declared by ``config``."""
    if config.strategy == "auto":
        return AutoChunker(max_tokens=config.max_tokens, overlap=config.overlap)
    if config.strategy == "sections":
        return SectionsChunker(max_tokens=config.max_tokens, overlap=config.overlap)
    if config.strategy == "size":
        return SizeChunker(max_chars=config.max_chars, overlap_chars=config.overlap_chars)
    raise ConfigError(f"chunk strategy {config.strategy!r} is not implemented")


def build_embedder(
    config: EmbedConfig,
    *,
    enable_cache: bool = True,
) -> Embedder:
    """Build the embedder declared by ``config``, optionally caching vectors."""
    if config.type == "default":
        delegate: Embedder = HashEmbedder()
    elif config.type == "openai":
        settings = dict(config.config)
        settings.pop("cache", None)
        delegate = ApiEmbedder(**settings)
    else:
        raise ConfigError(f"embed type {config.type!r} is not implemented")

    if not enable_cache or not config.cache_enabled:
        return delegate
    cache = (
        FileEmbeddingCache(config.cache_path)
        if config.cache_path
        else InMemoryEmbeddingCache()
    )
    return CachingEmbedder(delegate, cache=cache)


def build_indexer(config: IndexConfig) -> MemoryIndex | QdrantIndex | PgVectorIndex:
    """Build the vector index declared by ``config``."""
    if config.type == "memory":
        return MemoryIndex()
    if config.type == "qdrant":
        return QdrantIndex(**config.config)
    if config.type == "pgvector":
        return PgVectorIndex(**config.config)
    raise ConfigError(f"index type {config.type!r} is not implemented in v0.1")