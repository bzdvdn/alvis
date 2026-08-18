# Winnow

No-code knowledge ingestion engine. Build corporate knowledge bases from heterogeneous sources (GitLab, Confluence, S3; Markdown, HTML, PDF, DOCX, XLSX, CSV) without writing Python.

```
Source → Artifact → Extraction → Canonical Content Tree → Chunking → Embedding → Index
```

## Status

Alpha (v0.1.0). Core pipeline runs end-to-end: fs/Confluence/GitHub/GitLab/S3 sources → extract → chunk → embed → memory/Qdrant index. Async-native (httpx). The embedder (`default`) is a deterministic placeholder for v0.1; real models land in v1.0.

## Quick start

```bash
pip install -e .
winnow --version
winnow init                # scaffold winnow.yaml
winnow validate winnow.yaml
winnow run examples/hello-pipeline.yaml   # zero-dependency hello world
winnow run winnow.yaml --dry-run          # describe pipeline without running
```

## Sources

| type        | what it ingests                                              | config keys                                                                     |
| ----------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| `fs`        | text files under a directory                                 | `path`, `pattern`                                                               |
| `confluence`| pages of a space (REST API)                                  | `url`, `space`, `username`, `api_token_env`                                     |
| `github`    | blobs of a repository tree (REST API)                        | `repo`, `branch`, `path`, `include_globs`, `exclude_globs`, `api_token_env`     |
| `gitlab`    | blobs of a project repository (REST API; self-hosted OK)     | `url`, `project`, `branch`, `path`, `include_globs`, `exclude_globs`, `api_token_env` |
| `s3`        | text objects in a bucket (SigV4, no boto3; MinIO-compatible) | `url`, `bucket`, `access_key_env`, `secret_key_env`, `region`, `prefix`, `include_globs`, `exclude_globs` |

Every source accepts `retries`, `retry_backoff`, and `verify`.

**Scoping what gets ingested** — use `prefix`/`path` to restrict a directory or
subtree server-side, `include_globs` to ingest only matching paths, and
`exclude_globs` to skip garbage (videos, logs, vendor dirs); exclude wins over
include. With no globs, non-text files are filtered out automatically.

Example — ingest only `docs/**` from a bucket, skipping any media:

```yaml
pipeline:
  source:
    type: s3
    config:
      url: http://localhost:9000
      bucket: winnow
      access_key_env: MINIO_ACCESS_KEY
      secret_key_env: MINIO_SECRET_KEY
      prefix: docs
      include_globs: ["**/*.md", "**/*.rst"]
      exclude_globs: ["**/*.mp4", "**/*.mov"]
```

## End-to-end with docker-compose

Runs the full pipeline against real services (real Qdrant, mock Confluence,
MinIO):

```bash
docker compose up -d --build
winnow run examples/confluence-qdrant.yaml
winnow run examples/s3-qdrant.yaml        # needs MINIO_ACCESS_KEY / MINIO_SECRET_KEY env vars
# verify: points in the collections
curl http://localhost:6333/collections/winnow_docs
curl http://localhost:6333/collections/winnow-s3
docker compose down          # stop services
```

The mock Confluence implements the `/rest/api` contract our adapter consumes
(`docker/mock-confluence/`); swap its `url` for a real instance when ready.
MinIO is seeded (`minio-init`) with a `winnow` bucket containing sample docs
and a video that should be excluded (`examples/s3-seed/`).

Several pipelines run in a single command and execute concurrently; a failing
pipeline is reported and does not stop the others:

```bash
winnow run examples/confluence-qdrant.yaml examples/s3-qdrant.yaml --parallel 2
```

## Embedding in your application

The pipeline is just an async API, so it slots into workers, schedulers, or
web apps. `winnow` exposes thin sync/async entry points:

```python
from winnow import run, run_async, run_many, run_many_async

run("pipeline.yaml")                    # single pipeline, sync (celery/scripts)
await run_async("pipeline.yaml")        # single pipeline, async (FastAPI, ...)

results = run_many(["a.yaml", "b.yaml"], max_parallel=4)   # parallel, sync
await run_many_async(["a.yaml", "b.yaml"])                 # parallel, async
```

All variants return `PipelineResult(documents_ingested, chunks_indexed)`.
Parallel pipelines share one event loop; each pipeline owns its source and
index adapters (no shared mutable state). If multiple pipelines write to the
same Qdrant collection, give them distinct collections or source identities so
the per-source `reconcile` pass does not prune each other's points.

## Describing pipelines in Python

YAML is for the no-code UI; a thin typed DSL ("no config strings") builds the
exact same config objects, so a Python pipeline validates, dry-runs, and runs
identically to its YAML twin:

```python
from winnow import dsl, run

cfg = dsl.pipeline(
    dsl.s3(
        url="http://localhost:9000",
        bucket="winnow",
        access_key_env="MINIO_ACCESS_KEY",
        secret_key_env="MINIO_SECRET_KEY",
        prefix="docs",
        exclude_globs=["**/*.mp4"],
    ),
    chunk=dsl.chunk(max_tokens=80),
    index=dsl.qdrant(url="http://localhost:6333", collection="winnow-s3"),
)
run(cfg)                              # sync, handles its own event loop
await run_async(cfg)                  # or inside your own async app
```

Stage builders mirror the YAML schema: `dsl.fs`, `dsl.confluence`,
`dsl.github`, `dsl.gitlab` (self-hosted via `url`), `dsl.s3`,
`dsl.extract`, `dsl.chunk`, `dsl.embed`, `dsl.qdrant`, `dsl.memory`.
Defaults are omitted from the underlying config, and `pipeline(...)`
accepts only the stages you want to override.

Re-running a pipeline is idempotent: chunk point IDs are deterministic, so
identical content is overwritten, and a `reconcile` pass prunes points of
changed or deleted documents (verified live against Qdrant: 6→6 on re-run).

Transient failures are retried (429/5xx/connection issues) with exponential
backoff and jitter; `Retry-After` is honored. Per-source/index config accepts
`retries`, `retry_backoff`, and `verify: false` for self-signed HTTPS (use
the latter only against trusted internal endpoints).

## Documentation

- [Constitution](CONSTITUTION.md) — purpose, scope, open-source strategy
- [Roadmap](ROADMAP.md) — build plan
- `docs/` — guides (getting started, contributor guide, in progress)

## License

Apache 2.0. See [LICENSE](LICENSE).