"""Python DSL for describing pipelines — YAML-free, fully typed.

Builds the same :class:`~alvis.config.models.PipelineConfig` objects that
the declarative YAML loader produces, so a pipeline described in Python
validates, dry-runs, and runs identically to its YAML twin.

Example:

    from alvis import dsl, run

    cfg = dsl.pipeline(
        dsl.s3(
            url="http://localhost:9000",
            bucket="alvis",
            access_key_env="MINIO_ACCESS_KEY",
            secret_key_env="MINIO_SECRET_KEY",
            exclude_globs=["**/*.mp4"],
        ),
        chunk=dsl.chunk(max_tokens=80),
        index=dsl.qdrant(url="http://localhost:6333", collection="alvis-s3"),
    )
    run(cfg)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alvis.config.models import (
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
    max_bytes: int | None = None,
    acl: list[str] | None = None,
) -> SourceConfig:
    """Ingest text files under a directory (optionally matching ``pattern``).

    ``max_bytes`` skips files larger than this. ``acl`` (optional) is a
    static list of principal strings stamped onto every artifact this
    source fetches — see ``docs/schema.md`` and
    :class:`alvis.pipeline.stages.FetchStage`.
    """
    return SourceConfig(
        type="fs",
        config={
            "path": str(path),
            **({"pattern": pattern} if pattern else {}),
            **({"max_bytes": max_bytes} if max_bytes is not None else {}),
            **({"acl": acl} if acl else {}),
        },
    )


def confluence(
    url: str,
    space: str,
    username: str | None = None,
    api_token_env: str | None = None,
    retries: int = 3,
    retry_backoff: float = 1.0,
    verify: bool | str = True,
    max_concurrency: int = 8,
    acl: list[str] | None = None,
) -> SourceConfig:
    """Ingest pages of a Confluence space via its REST API.

    ``max_concurrency`` bounds concurrent page-expansion requests.
    ``acl`` (optional) is a static list of principal strings stamped onto
    every artifact this source fetches — see ``docs/schema.md``.
    """
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
            **({"max_concurrency": max_concurrency} if max_concurrency != 8 else {}),
            **({"acl": acl} if acl else {}),
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
    max_bytes: int | None = None,
    max_concurrency: int = 8,
    acl: list[str] | None = None,
) -> SourceConfig:
    """Ingest blobs of a GitHub repository tree.

    ``max_bytes`` skips blobs larger than this. ``max_concurrency`` bounds
    concurrent blob downloads. ``acl`` (optional) is a static list of
    principal strings stamped onto every artifact this source fetches —
    see ``docs/schema.md``.
    """
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
            **({"max_bytes": max_bytes} if max_bytes is not None else {}),
            **({"max_concurrency": max_concurrency} if max_concurrency != 8 else {}),
            **({"acl": acl} if acl else {}),
        },
    )


def gitlab(
    project: str | None = None,
    group: str | None = None,
    url: str = "https://gitlab.com",
    branch: str = "main",
    path: str | None = None,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    project_include_globs: list[str] | None = None,
    project_exclude_globs: list[str] | None = None,
    include_archived: bool = False,
    api_token_env: str | None = None,
    per_page: int = 100,
    retries: int = 3,
    retry_backoff: float = 1.0,
    verify: bool | str = True,
    max_bytes: int | None = None,
    max_concurrency: int = 8,
    acl: list[str] | None = None,
) -> SourceConfig:
    """Ingest blobs of a GitLab project (works with self-hosted instances).

    Pass ``project`` for a single repository, or ``group`` to traverse every
    project in a group. Use ``project_include_globs`` / ``project_exclude_globs``
    to narrow which group repositories are ingested (matched against the full
    ``group/project`` path). ``max_bytes`` skips blobs larger than this;
    ``max_concurrency`` bounds concurrent blob downloads. ``acl`` (optional)
    is a static list of principal strings stamped onto every artifact this
    source fetches.
    """
    if project is None and group is None:
        raise ValueError("gitlab() requires 'project' or 'group'")
    return SourceConfig(
        type="gitlab",
        config={
            **({"project": project} if project else {}),
            **({"group": group} if group else {}),
            **({"url": url} if url != "https://gitlab.com" else {}),
            **({"branch": branch} if branch != "main" else {}),
            **({"path": path} if path else {}),
            **({"include_globs": include_globs} if include_globs else {}),
            **({"exclude_globs": exclude_globs} if exclude_globs else {}),
            **(
                {"project_include_globs": project_include_globs}
                if project_include_globs
                else {}
            ),
            **(
                {"project_exclude_globs": project_exclude_globs}
                if project_exclude_globs
                else {}
            ),
            **({"include_archived": include_archived} if include_archived else {}),
            **({"api_token_env": api_token_env} if api_token_env else {}),
            **({"per_page": per_page} if per_page != 100 else {}),
            **({"retries": retries} if retries != 3 else {}),
            **({"retry_backoff": retry_backoff} if retry_backoff != 1.0 else {}),
            **({"verify": verify} if verify is not True else {}),
            **({"max_bytes": max_bytes} if max_bytes is not None else {}),
            **({"max_concurrency": max_concurrency} if max_concurrency != 8 else {}),
            **({"acl": acl} if acl else {}),
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
    max_bytes: int | None = None,
    max_concurrency: int = 8,
    acl: list[str] | None = None,
) -> SourceConfig:
    """Ingest text objects from an S3-compatible bucket (SigV4, no boto3).

    ``max_bytes`` skips objects larger than this. ``max_concurrency`` bounds
    concurrent object downloads. ``acl`` (optional) is a static list of
    principal strings stamped onto every artifact this source fetches.
    """
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
            **({"max_bytes": max_bytes} if max_bytes is not None else {}),
            **({"max_concurrency": max_concurrency} if max_concurrency != 8 else {}),
            **({"acl": acl} if acl else {}),
        },
    )


def static_url(
    urls: list[str],
    api_token_env: str | None = None,
    timeout: float = 20.0,
    max_bytes: int | None = None,
    retries: int = 3,
    retry_backoff: float = 1.0,
    verify: bool | str = True,
    max_concurrency: int = 8,
    acl: list[str] | None = None,
) -> SourceConfig:
    """Ingest plain HTML (or other text) pages served over HTTP(S), no JS.

    ``max_concurrency`` bounds concurrent page downloads. ``acl``
    (optional) is a static list of principal strings stamped onto every
    artifact this source fetches.
    """
    return SourceConfig(
        type="static_url",
        config={
            "urls": urls,
            **({"api_token_env": api_token_env} if api_token_env else {}),
            **({"timeout": timeout} if timeout != 20.0 else {}),
            **({"max_bytes": max_bytes} if max_bytes is not None else {}),
            **({"retries": retries} if retries != 3 else {}),
            **({"retry_backoff": retry_backoff} if retry_backoff != 1.0 else {}),
            **({"verify": verify} if verify is not True else {}),
            **({"max_concurrency": max_concurrency} if max_concurrency != 8 else {}),
            **({"acl": acl} if acl else {}),
        },
    )


def chunk(
    strategy: str = "auto",
    max_tokens: int = 500,
    overlap: int = 50,
    max_chars: int = 2000,
    overlap_chars: int = 200,
) -> ChunkConfig:
    """Auto chunking.

    ``strategy="auto"`` splits sections by token budget with overlap;
    ``strategy="sections"`` produces one chunk per heading, repeating the
    heading as context on oversized-section continuations;
    ``strategy="size"`` cuts on a fixed character budget (``max_chars``)
    with ``overlap_chars`` overlap, ignoring document structure.
    """
    settings: dict[str, Any] = {}
    if max_tokens != 500:
        settings["max_tokens"] = max_tokens
    if overlap != 50:
        settings["overlap"] = overlap
    if max_chars != 2000:
        settings["max_chars"] = max_chars
    if overlap_chars != 200:
        settings["overlap_chars"] = overlap_chars
    return ChunkConfig(
        strategy=strategy,
        config=settings,
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
    max_concurrency: int = 4,
    retries: int = 3,
    retry_backoff: float = 1.0,
    verify: bool | str = True,
    cache: bool | dict[str, Any] | None = None,
) -> EmbedConfig:
    """Embed via an OpenAI-compatible ``/embeddings`` endpoint.

    Requires the endpoint to return OpenAI's response shape
    (``data[].embedding``); ``api_token_env`` is sent as ``Bearer``.
    ``max_concurrency`` bounds how many ``/embeddings`` batches are in
    flight at once. ``cache`` enables embedding reuse: ``True`` caches in
    memory, a ``{"path": "..."}`` dict persists to disk across runs.
    """
    return EmbedConfig(
        type="openai",
        config={
            "base_url": base_url,
            "model": model,
            **({"api_token_env": api_token_env} if api_token_env else {}),
            **({"batch_size": batch_size} if batch_size != 32 else {}),
            **({"max_concurrency": max_concurrency} if max_concurrency != 4 else {}),
            **({"retries": retries} if retries != 3 else {}),
            **({"retry_backoff": retry_backoff} if retry_backoff != 1.0 else {}),
            **({"verify": verify} if verify is not True else {}),
            **({"cache": cache} if cache is not None else {}),
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


def elasticsearch(
    url: str,
    index: str,
    api_token_env: str | None = None,
    username: str | None = None,
    retries: int = 3,
    retry_backoff: float = 1.0,
    verify: bool | str = True,
) -> IndexConfig:
    """Store vectors in an Elasticsearch index (dense_vector + kNN, ES 8.0+).

    Targets Elasticsearch's native ``knn`` search specifically, not
    OpenSearch's separate k-NN plugin dialect — see
    :mod:`alvis.index.elasticsearch`.
    """
    return IndexConfig(
        type="elasticsearch",
        config={
            "url": url,
            "index": index,
            **({"api_token_env": api_token_env} if api_token_env else {}),
            **({"username": username} if username else {}),
            **({"retries": retries} if retries != 3 else {}),
            **({"retry_backoff": retry_backoff} if retry_backoff != 1.0 else {}),
            **({"verify": verify} if verify is not True else {}),
        },
    )


def memory() -> IndexConfig:
    """In-memory index (tests, small prototypes)."""
    return IndexConfig(type="memory")


def sqlite(path: str = "alvis.db") -> IndexConfig:
    """Store vectors in a persistent single-file SQLite database.

    No extra dependencies; ideal for dev and small prototypes that need to
    survive process restarts. ``path`` is a ``.db`` file, or ``:memory:`` for
    an ephemeral store.
    """
    return IndexConfig(type="sqlite", config={"path": path})


def pgvector(
    dsn: str | None = None,
    dsn_env: str | None = None,
    table: str = "alvis_chunks",
) -> IndexConfig:
    """Store vectors in a pgvector column (requires ``alvis[pgindex]``).

    ``dsn`` is a psycopg connection string (``postgresql://user@host/db``);
    ``dsn_env`` names an environment variable holding it instead. The table
    is created on demand with a ``vector(N)`` column matching the model.
    """
    return IndexConfig(
        type="pgvector",
        config={
            **({"dsn": dsn} if dsn else {}),
            **({"dsn_env": dsn_env} if dsn_env else {}),
            **({"table": table} if table != "alvis_chunks" else {}),
        },
    )


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