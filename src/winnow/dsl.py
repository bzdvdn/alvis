"""Python DSL for describing pipelines — YAML-free, fully typed.

Builds the same :class:`~winnow.config.models.PipelineConfig` objects that
the declarative YAML loader produces, so a pipeline described in Python
validates, dry-runs, and runs identically to its YAML twin.

Example:

    from winnow import dsl, run

    cfg = dsl.pipeline(
        dsl.s3(
            url="http://localhost:9000",
            bucket="winnow",
            access_key_env="MINIO_ACCESS_KEY",
            secret_key_env="MINIO_SECRET_KEY",
            exclude_globs=["**/*.mp4"],
        ),
        chunk=dsl.chunk(max_tokens=80),
        index=dsl.qdrant(url="http://localhost:6333", collection="winnow-s3"),
    )
    run(cfg)
"""

from __future__ import annotations

from pathlib import Path

from winnow.config.models import (
    ChunkConfig,
    EmbedConfig,
    ExtractConfig,
    IndexConfig,
    PipelineConfig,
    SourceConfig,
)


def fs(
    path: str | Path,
    pattern: str | None = None,
) -> SourceConfig:
    """Ingest text files under a directory (optionally matching ``pattern``)."""
    return SourceConfig(
        type="fs",
        config={"path": str(path), **({"pattern": pattern} if pattern else {})},
    )


def confluence(
    url: str,
    space: str,
    username: str | None = None,
    api_token_env: str | None = None,
    retries: int = 3,
    retry_backoff: float = 1.0,
    verify: bool | str = True,
) -> SourceConfig:
    """Ingest pages of a Confluence space via its REST API."""
    return SourceConfig(
        type="confluence",
        config={
            "url": url,
            "space": space,
            **({"username": username} if username else {}),
            **({"api_token_env": api_token_env} if api_token_env else {}),
            **({"retries": retries} if retries != 3 else {}),
            **({"retry_backoff": retry_backoff} if retry_backoff != 1.0 else {}),
            **({"verify": verify} if verify is not True else {}),
        },
    )


def github(
    repo: str,
    branch: str = "main",
    path: str | None = None,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    api_token_env: str | None = None,
    retries: int = 3,
    retry_backoff: float = 1.0,
    verify: bool | str = True,
) -> SourceConfig:
    """Ingest blobs of a GitHub repository tree."""
    return SourceConfig(
        type="github",
        config={
            "repo": repo,
            **({"branch": branch} if branch != "main" else {}),
            **({"path": path} if path else {}),
            **({"include_globs": include_globs} if include_globs else {}),
            **({"exclude_globs": exclude_globs} if exclude_globs else {}),
            **({"api_token_env": api_token_env} if api_token_env else {}),
            **({"retries": retries} if retries != 3 else {}),
            **({"retry_backoff": retry_backoff} if retry_backoff != 1.0 else {}),
            **({"verify": verify} if verify is not True else {}),
        },
    )


def gitlab(
    project: str,
    url: str = "https://gitlab.com",
    branch: str = "main",
    path: str | None = None,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    api_token_env: str | None = None,
    per_page: int = 100,
    retries: int = 3,
    retry_backoff: float = 1.0,
    verify: bool | str = True,
) -> SourceConfig:
    """Ingest blobs of a GitLab project (works with self-hosted instances)."""
    return SourceConfig(
        type="gitlab",
        config={
            "project": project,
            **({"url": url} if url != "https://gitlab.com" else {}),
            **({"branch": branch} if branch != "main" else {}),
            **({"path": path} if path else {}),
            **({"include_globs": include_globs} if include_globs else {}),
            **({"exclude_globs": exclude_globs} if exclude_globs else {}),
            **({"api_token_env": api_token_env} if api_token_env else {}),
            **({"per_page": per_page} if per_page != 100 else {}),
            **({"retries": retries} if retries != 3 else {}),
            **({"retry_backoff": retry_backoff} if retry_backoff != 1.0 else {}),
            **({"verify": verify} if verify is not True else {}),
        },
    )


def s3(
    url: str,
    bucket: str,
    access_key_env: str,
    secret_key_env: str,
    region: str = "us-east-1",
    prefix: str | None = None,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    retries: int = 3,
    retry_backoff: float = 1.0,
    verify: bool | str = True,
) -> SourceConfig:
    """Ingest text objects from an S3-compatible bucket (SigV4, no boto3)."""
    return SourceConfig(
        type="s3",
        config={
            "url": url,
            "bucket": bucket,
            "access_key_env": access_key_env,
            "secret_key_env": secret_key_env,
            **({"region": region} if region != "us-east-1" else {}),
            **({"prefix": prefix} if prefix else {}),
            **({"include_globs": include_globs} if include_globs else {}),
            **({"exclude_globs": exclude_globs} if exclude_globs else {}),
            **({"retries": retries} if retries != 3 else {}),
            **({"retry_backoff": retry_backoff} if retry_backoff != 1.0 else {}),
            **({"verify": verify} if verify is not True else {}),
        },
    )


def chunk(max_tokens: int = 500, overlap: int = 50) -> ChunkConfig:
    """Auto chunking by approximate token size with overlap."""
    return ChunkConfig(
        strategy="auto",
        config={
            **({"max_tokens": max_tokens} if max_tokens != 500 else {}),
            **({"overlap": overlap} if overlap != 50 else {}),
        },
    )


def extract() -> ExtractConfig:
    """Auto extraction (markdown/html/plain text to canonical tree)."""
    return ExtractConfig(strategy="auto")


def embed() -> EmbedConfig:
    """Default (deterministic placeholder) embedder."""
    return EmbedConfig(type="default")


def embed_openai(
    base_url: str,
    model: str,
    api_token_env: str | None = None,
    batch_size: int = 32,
    retries: int = 3,
    retry_backoff: float = 1.0,
    verify: bool | str = True,
) -> EmbedConfig:
    """Embed via an OpenAI-compatible ``/embeddings`` endpoint.

    Requires the endpoint to return OpenAI's response shape
    (``data[].embedding``); ``api_token_env`` is sent as ``Bearer``.
    """
    return EmbedConfig(
        type="openai",
        config={
            "base_url": base_url,
            "model": model,
            **({"api_token_env": api_token_env} if api_token_env else {}),
            **({"batch_size": batch_size} if batch_size != 32 else {}),
            **({"retries": retries} if retries != 3 else {}),
            **({"retry_backoff": retry_backoff} if retry_backoff != 1.0 else {}),
            **({"verify": verify} if verify is not True else {}),
        },
    )


def qdrant(
    url: str,
    collection: str,
    api_token_env: str | None = None,
    retries: int = 3,
    retry_backoff: float = 1.0,
    verify: bool | str = True,
) -> IndexConfig:
    """Store vectors in a Qdrant collection (created on demand)."""
    return IndexConfig(
        type="qdrant",
        config={
            "url": url,
            "collection": collection,
            **({"api_token_env": api_token_env} if api_token_env else {}),
            **({"retries": retries} if retries != 3 else {}),
            **({"retry_backoff": retry_backoff} if retry_backoff != 1.0 else {}),
            **({"verify": verify} if verify is not True else {}),
        },
    )


def memory() -> IndexConfig:
    """In-memory index (tests, small prototypes)."""
    return IndexConfig(type="memory")


def pipeline(
    source: SourceConfig,
    chunk: ChunkConfig | None = None,
    index: IndexConfig | None = None,
    extract: ExtractConfig | None = None,
    embed: EmbedConfig | None = None,
) -> PipelineConfig:
    """Assemble a pipeline from stage configs."""
    return PipelineConfig(
        source=source,
        extract=extract or ExtractConfig(),
        chunk=chunk or ChunkConfig(),
        embed=embed or EmbedConfig(),
        index=index,
    )