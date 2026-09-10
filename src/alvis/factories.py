"""Build stage adapters from declarative config.

Built-in types map directly in this module; anything else consults the plugin
registry (roadmap v1.1), so an installed plugin's type strings are resolved
here without any core change. Plugin factories follow the contract documented
in :mod:`alvis.plugin`.
"""

from __future__ import annotations

from alvis.chunk import AutoChunker, Chunker, SectionsChunker, SizeChunker
from alvis.config.loader import ConfigError
from alvis.config.models import (
    ChunkConfig,
    EmbedConfig,
    ExtractConfig,
    IndexConfig,
    SourceConfig,
)
from alvis.embed import (
    ApiEmbedder,
    CachingEmbedder,
    Embedder,
    FileEmbeddingCache,
    HashEmbedder,
    InMemoryEmbeddingCache,
)
from alvis.extract import AutoExtractor
from alvis.extract.base import Extractor
from alvis.index import ElasticsearchIndex, MemoryIndex, PgVectorIndex, QdrantIndex, SqliteIndex
from alvis.index.base import Indexer
from alvis.plugin import registry
from alvis.registry import known_sources
from alvis.sources import (
    ConfluenceSource,
    FilesystemSource,
    GitHubSource,
    GitLabSource,
    GoogleDriveSource,
    JiraSource,
    NotionSource,
    S3Source,
    SharePointSource,
    StaticUrlSource,
)
from alvis.sources.base import Source


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
) -> Source:
    """Build the source adapter declared by ``config``.

    ``acl`` (if present in ``config.config``) is a reserved key read
    directly by :class:`alvis.pipeline.stages.FetchStage` to stamp every
    artifact's metadata — it is never passed to a built-in source's
    constructor.

    ``max_bytes`` here is the engine-level cap (``cfg.extract.max_bytes``);
    a source's own ``config.config`` may *also* declare a per-source
    ``max_bytes`` (e.g. via ``dsl.github(max_bytes=...)``). The per-source
    value, when given, wins — it's popped out of ``settings`` first so it
    is never forwarded twice (as both part of ``**settings`` and the
    explicit ``max_bytes=`` kwarg below, which previously raised "got
    multiple values for argument 'max_bytes'").
    """
    settings = {key: value for key, value in config.config.items() if key != "acl"}
    source_max_bytes = settings.pop("max_bytes", None)
    if source_max_bytes is not None:
        max_bytes = source_max_bytes
    if config.type == "fs":
        return FilesystemSource(**settings, max_bytes=max_bytes)
    if config.type == "confluence":
        return ConfluenceSource(**settings)
    if config.type == "github":
        return GitHubSource(**settings, max_bytes=max_bytes)
    if config.type == "gitlab":
        return GitLabSource(**settings, max_bytes=max_bytes)
    if config.type == "s3":
        return S3Source(**settings, max_bytes=max_bytes)
    if config.type == "static_url":
        return StaticUrlSource(**settings, max_bytes=max_bytes)
    if config.type == "notion":
        return NotionSource(**settings)
    if config.type == "jira":
        return JiraSource(**settings)
    if config.type == "sharepoint":
        return SharePointSource(**settings, max_bytes=max_bytes)
    if config.type == "gdrive":
        return GoogleDriveSource(**settings, max_bytes=max_bytes)
    plugin_factory = registry().factory("source", config.type)
    if plugin_factory is not None:
        return plugin_factory(config=config, max_bytes=max_bytes)  # type: ignore[return-value]
    raise ConfigError(
        f"source type {config.type!r} is not implemented "
        f"(known: {sorted(known_sources())}; is it an installed plugin?)"
    )


def build_extractor(config: ExtractConfig) -> Extractor:
    """Build the extraction strategy declared by ``config``."""
    if config.strategy == "auto":
        return AutoExtractor()
    plugin_factory = registry().factory("extractor", config.strategy)
    if plugin_factory is not None:
        return plugin_factory(config=config)  # type: ignore[return-value]
    raise ConfigError(f"extract strategy {config.strategy!r} is not implemented")


def build_chunker(config: ChunkConfig) -> Chunker:
    """Build the chunking strategy declared by ``config``."""
    if config.strategy == "auto":
        return AutoChunker(max_tokens=config.max_tokens, overlap=config.overlap)
    if config.strategy == "sections":
        return SectionsChunker(max_tokens=config.max_tokens, overlap=config.overlap)
    if config.strategy == "size":
        return SizeChunker(max_chars=config.max_chars, overlap_chars=config.overlap_chars)
    plugin_factory = registry().factory("chunker", config.strategy)
    if plugin_factory is not None:
        return plugin_factory(config=config)  # type: ignore[return-value]
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
        plugin_factory = registry().factory("embedder", config.type)
        if plugin_factory is None:
            raise ConfigError(f"embed type {config.type!r} is not implemented")
        delegate = plugin_factory(config=config)  # type: ignore[assignment]

    if not enable_cache or not config.cache_enabled:
        return delegate
    cache = (
        FileEmbeddingCache(config.cache_path)
        if config.cache_path
        else InMemoryEmbeddingCache()
    )
    return CachingEmbedder(delegate, cache=cache)


def build_indexer(config: IndexConfig) -> Indexer:
    """Build the vector index declared by ``config``."""
    if config.type == "memory":
        return MemoryIndex()
    if config.type == "qdrant":
        return QdrantIndex(**config.config)
    if config.type == "pgvector":
        return PgVectorIndex(**config.config)
    if config.type == "sqlite":
        return SqliteIndex(**config.config)
    if config.type == "elasticsearch":
        return ElasticsearchIndex(**config.config)
    plugin_factory = registry().factory("indexer", config.type)
    if plugin_factory is not None:
        return plugin_factory(config=config)  # type: ignore[return-value]
    raise ConfigError(f"index type {config.type!r} is not implemented")