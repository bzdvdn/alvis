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