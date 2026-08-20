# Alvis

Knowledge ingestion and retrieval engine. Build corporate knowledge bases from heterogeneous sources (GitLab, Confluence, S3; Markdown, HTML, PDF, DOCX, XLSX, CSV) driven by a YAML pipeline — or described from Python with the typed DSL.

```
Source → Artifact → Extraction → Canonical Content Tree → Chunking → Embedding → Index
                                                Index → nearest-neighbour → SearchHit
```

## Status

v0.6.0. Core pipeline runs end-to-end: fs/Confluence/GitHub/GitLab/S3/static-URL sources → extract → chunk → embed → memory/Qdrant/pgvector index, plus vector retrieval (`alvis.query`, CLI `alvis query`). Async-native (httpx). The embedder ships a deterministic placeholder (`default`) plus an OpenAI-compatible API adapter (`openai`).

## Formats

Extraction (`strategy: auto`) understands Markdown, HTML, and plain text out
of the box, and PDF/DOCX/XLSX when the optional extra is installed:

```bash
pip install -e .[documents]
```

PDF pages become `Page N` sections, DOCX headings become section headings,
and each XLSX worksheet becomes a `<sheet title>` section with `|`-joined
cells. JSON is parsed structurally: every scalar becomes a section headed by
its dotted path (`user.address.city`), arrays indexed (`items.0`), so each
fact is independently retrievable. CSV yields one `Row N` section per line,
streamed lazily; a text first row is used as a header so rows read as
`name: Ada | team: core`. YAML/TOML are ingested as plain text.

Large binaries are handled safely: parsing runs in a worker thread, XLSX
rows stream lazily (`read_only`), and an optional artifact budget skips
oversized files without reading them into memory:

```yaml
pipeline:
  source:
    type: fs
    config:
      path: ./docs
  extract:
    config:
      max_bytes: 10485760   # skip artifacts > 10 MiB
  index:
    type: memory
```

The HTTP sources (GitLab/S3/GitHub) stream bodies through the same cap and
skip oversize objects instead of buffering them.

## Contracts

Both public contracts are versioned and frozen at v1: the **YAML schema**
(`pipeline.schema_version`, default `1`) and the **Canonical Content Tree**
(`Document.schema_version`). Old configs and trees load unchanged; unknown
versions fail fast. The versioning and backward-compatible evolution policy
live in [docs/schema.md](docs/schema.md).

## Quick start

```bash
pip install -e .
alvis --version
alvis init                # scaffold alvis.yaml
alvis validate alvis.yaml              # validation report + pipeline graph
alvis validate alvis.yaml --json       # machine-readable report for CI
alvis run examples/hello-pipeline.yaml   # zero-dependency hello world
alvis run alvis.yaml --dry-run          # describe pipeline without running
alvis status examples/hello-pipeline.yaml  # source health, last run, index count
```

Secrets are referenced by name (`api_token_env`, `dsn_env`, ...) from a
`.env` in the project root (or `--env-file`); `alvis run` fails fast listing
whatever is missing. See [docs/status.md](docs/status.md).

## Sources

| type        | what it ingests                                              | config keys                                                                     |
| ----------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| `fs`        | text files under a directory                                 | `path`, `pattern`                                                               |
| `confluence`| pages of a space (REST API)                                  | `url`, `space`, `username`, `api_token_env`                                     |
| `github`    | blobs of a repository tree (REST API)                        | `repo`, `branch`, `path`, `include_globs`, `exclude_globs`, `api_token_env`     |
| `gitlab`    | blobs of a project repository (REST API; self-hosted OK)     | `url`, `project`, `branch`, `path`, `include_globs`, `exclude_globs`, `api_token_env` |
| `s3`        | text objects in a bucket (SigV4, no boto3; MinIO-compatible) | `url`, `bucket`, `access_key_env`, `secret_key_env`, `region`, `prefix`, `include_globs`, `exclude_globs` |
| `static_url`| plain HTML pages served over HTTP(S), no JS needed            | `urls`, `api_token_env`, `max_bytes`, `timeout`                                                  |

Every source accepts `retries`, `retry_backoff`, and `verify`.

**Scoping what gets ingested** — use `prefix`/`path` to restrict a directory or
subtree server-side, `include_globs` to ingest only matching paths, and
`exclude_globs` to skip garbage (videos, logs, vendor dirs); exclude wins over
include. With no globs, non-text files are filtered out automatically.

`static_url` is incremental by default: pages are fingerprinted with their
`ETag`/`Last-Modified` during a cheap `HEAD` (falling back to a content hash
when a server serves neither), so unchanged pages are never re-downloaded and
removed pages are pruned by the per-source reconcile pass.

Example — ingest only `docs/**` from a bucket, skipping any media:

```yaml
pipeline:
  source:
    type: s3
    config:
      url: http://localhost:9000
      bucket: alvis
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
alvis run examples/confluence-qdrant.yaml
alvis run examples/s3-qdrant.yaml        # needs MINIO_ACCESS_KEY / MINIO_SECRET_KEY env vars
# verify: points in the collections
curl http://localhost:6333/collections/alvis_docs
curl http://localhost:6333/collections/alvis-s3
docker compose down          # stop services
```

The mock Confluence implements the `/rest/api` contract our adapter consumes
(`docker/mock-confluence/`); swap its `url` for a real instance when ready.
MinIO is seeded (`minio-init`) with a `alvis` bucket containing sample docs
and a video that should be excluded (`examples/s3-seed/`).

Several pipelines run in a single command and execute concurrently; a failing
pipeline is reported and does not stop the others:

```bash
alvis run examples/confluence-qdrant.yaml examples/s3-qdrant.yaml --parallel 2
```

For a zero-touch *monitoring* demo (Qdrant + Alvis watch mode + Prometheus +
Grafana with a ready-made dashboard), see
[`docker/observability/`](docker/observability/).

### Default pipelines folder

With no paths, `alvis run` scans `alvis/pipelines/*.yaml` (plus a top-level
`alvis.yaml`) and runs them concurrently — a project keeps its workflows as
plain files in one folder, ready for a mounted container or a scheduler:

```bash
alvis/pipelines/
  confluence.yaml
  s3.yaml
alvis run                # runs both, in parallel
```

### Local companion plugins

Plugins normally ship as installable packages. For uninstalled, in-repo code,
point `--plugins <dir>` at a directory of `.py` files: each file is loaded and
expected to assign a module-level `plugin` (a `alvis.plugin.Plugin`) or call
`install_plugin()` itself. This runs local code, so it is always explicit —
never auto-scanned:

```bash
alvis run --plugins ./plugins                 # project-local adapters
alvis validate catalog.yaml --plugins ./plugins
alvis plugins --plugins ./plugins             # list them
```

## Run as a container

Since a pipeline is just YAML, a ready-made image ships the whole product: a
`alvis` CLI with every optional extra (documents parsers, pgvector,
observability) built in. You configure it by mounting files — never by
rebuilding:

```bash
docker build -t alvis:dev .
docker run --rm \
  -v "$PWD/pipeline.yaml:/workspace/pipeline.yaml:ro" \
  -v "$PWD/corpus:/workspace/corpus:ro" \
  -v "$PWD/.alvis:/workspace/.alvis" \
  alvis:dev run --incremental pipeline.yaml
```

Mount a project instead and the defaults kick in: `alvis/pipelines/*.yaml` is
scanned with no args, and local adapters load with `--plugins`:

```bash
docker run --rm -v "$PWD:/workspace" alvis:dev run --plugins ./plugins
```

CI builds and publishes `bzdvdn/alvis` (linux/amd64 + linux/arm64) for every
`v*` tag with that tag plus `latest` — so on release you skip the local build
and just pull:

```bash
docker pull bzdvdn/alvis:v0.6.0
```

The image runs as an unprivileged `alvis` user in `/workspace`; incremental
state lands in `.alvis/state.json` there, so the volume above is what makes
re-runs skip unchanged content. Point a `--watch` config at a scheduler and the
container *is* the ingestion loop:

```bash
docker run -d --name alvis-watcher --restart unless-stopped \
  -v "$PWD/pipeline.yaml:/workspace/pipeline.yaml:ro" \
  -v "$PWD/corpus:/workspace/corpus:ro" \
  -v "$PWD/.alvis:/workspace/.alvis" \
  alvis:dev run --watch --interval 60 pipeline.yaml
```

## Embedding

`embed.type: default` is a deterministic placeholder for local/dev work.
For real vectors use the OpenAI-compatible `openai` embedder (works with
OpenAI, Azure, local servers):

```yaml
pipeline:
  source:
    type: fs
    config:
      path: ./docs
  embed:
    type: openai
    config:
      base_url: https://api.openai.com/v1
      model: text-embedding-3-small
      api_token_env: OPENAI_API_KEY
      batch_size: 32
  index:
    type: qdrant
    config:
      url: http://localhost:6333
      collection: alvis_docs
```

Chunks are embedded in batches (`batch_size`) through the same
retry/backoff machinery as sources, and the Qdrant collection is created
with whatever dimensionality the model returns.

#### Embedding cache

Re-running a pipeline re-embeds every chunk unless you enable the cache.
`cache: true` keeps vectors in memory for the process; a dict with `path`
persists them to disk, so unchanged chunks are served from a previous run
instead of hitting the embeddings endpoint:

```yaml
pipeline:
  source:
    type: fs
    config:
      path: ./docs
  embed:
    type: openai
    config:
      base_url: https://api.openai.com/v1
      model: text-embedding-3-small
      api_token_env: OPENAI_API_KEY
      cache:
        path: .alvis/embeddings.cache
  index:
    type: qdrant
    config:
      url: http://localhost:6333
      collection: alvis_docs
```

Keys are derived from the content hash **and** the model signature, so
switching models never serves stale vectors. Runs report hit/miss counts:

```text
pipeline.yaml: 12 documents, 87 chunks indexed
  embed cache: 85 hits, 2 misses
```

### Chunk strategies

- `auto` — paragraph-aware token-budget splitting with overlap (default).
- `sections` — one chunk per section heading; oversized sections are split
  on the budget with the heading repeated as context on continuations.
- `size` — pure character-budget splitting (`max_chars`, `overlap_chars`)
  for unstructured sources with no reliable headings.

### Indexes

- `qdrant` — Qdrant vector store (`url`, `collection`).
- `pgvector` — PostgreSQL + pgvector column
  (`dsn` / `dsn_env`, `table`, requires `pip install alvis[pgindex]`).
  See `examples/pgvector.yaml` and the `postgres` service in docker-compose.
- `memory` — in-memory store for tests and prototypes.

### Retrieval

Ask questions against an already-loaded index; the query string is embedded
with the config's embedder and searched nearest-neighbour:

```bash
alvis query examples/pgvector.yaml --text "how do I install alvis?" --top-k 5
```

`--answer` synthesizes a cited answer over the top hits instead of just
returning chunks — works with an OpenAI-compatible endpoint
(`--answer-base-url/--answer-model/--answer-api-token-env`, defaults point at
OpenAI), and falls back to numbered excerpts when no API key is set:

```bash
alvis query examples/pgvector.yaml --text "how do I install alvis?" --answer
```

Programmatically (same contract as ingestion):

```python
from alvis import query, query_async, answer, answer_async, Synthesizer

hits = query("examples/pgvector.yaml", "how do I install alvis?", top_k=5)
await query_async("examples/pgvector.yaml", "how do I install alvis?")

# cited answer over the hits, with an explicit LLM client
llm = Synthesizer(base_url="https://api.openai.com/v1", model="gpt-4o-mini",
                  api_token_env="OPENAI_API_KEY")
result = answer("examples/pgvector.yaml", "how do I install alvis?", llm=llm)
result.text                      # "Run `pip install alvis` [1], ..."
result.citations                 # [Citation(index=1, source_uri=..., ...)]
```

`SearchHit` carries `text`, `source_uri`, `metadata`, and a cosine `score`
(best first). For in-memory indexes pass the same `indexer` instance to
`run`/`query` (persistent backends rebuild their clients from config).

## Embedding in your application

The pipeline is just an async API, so it slots into workers, schedulers, or
web apps. `alvis` exposes thin sync/async entry points:

```python
from alvis import run, run_async, run_many, run_many_async

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

YAML is the declarative interface (no code at all); the typed Python DSL
("no config strings") is a first-class way to describe the exact same
pipeline from code, so a Python pipeline validates, dry-runs, and runs
identically to its YAML twin:

```python
from alvis import dsl, run

cfg = dsl.pipeline(
    dsl.s3(
        url="http://localhost:9000",
        bucket="alvis",
        access_key_env="MINIO_ACCESS_KEY",
        secret_key_env="MINIO_SECRET_KEY",
        prefix="docs",
        exclude_globs=["**/*.mp4"],
    ),
    chunk=dsl.chunk(max_tokens=80),
    index=dsl.qdrant(url="http://localhost:6333", collection="alvis-s3"),
)
run(cfg)                              # sync, handles its own event loop
await run_async(cfg)                  # or inside your own async app
```

Stage builders mirror the YAML schema: `dsl.fs`, `dsl.confluence`,
`dsl.github`, `dsl.gitlab` (self-hosted via `url`), `dsl.s3`,
`dsl.extract`, `dsl.chunk`, `dsl.embed`, `dsl.embed_openai`, `dsl.qdrant`,
`dsl.memory`. Defaults are omitted from the underlying config, and
`pipeline(...)` accepts only the stages you want to override. See
[docs/dsl.md](docs/dsl.md) and [examples/python_dsl.py](examples/python_dsl.py).

Re-running a pipeline is idempotent: chunk point IDs are deterministic, so
identical content is overwritten, and a `reconcile` pass prunes points of
changed or deleted documents (verified live against Qdrant: 6→6 on re-run).

#### Payload contract

The Qdrant payload splits into a reserved system namespace and your own
metadata. Keys beginning with `__` are owned by the engine and drive dedup,
incremental skip, and the per-source `reconcile` pass:

| key              | meaning                                                            |
| ---------------- | ------------------------------------------------------------------ |
| `__text`         | chunk text (returned by `SearchHit.text`)                          |
| `__uri`          | source URI — provenance (returned by `SearchHit.source_uri`)       |
| `__source`       | source identity, namespaces the `reconcile` prune                   |
| `__hash`         | content hash, powers skip-unchanged and prune                       |
| `__document_id`  | stable document identity for citations/per-doc rules                |
| `__schema`       | payload schema version (currently 1)                                |

Everything else is your namespace: metadata is written verbatim and returned
in `SearchHit.metadata`. The engine reserves the `__` prefix — metadata keys
starting with `__` are rejected on write so user data can never overwrite
system fields. A metadata key named `documentId` (or `document_id`) is
promoted to `__document_id`; sources with a natural stable id declare it
(GitLab/GitHub blob id, Confluence page id) so renames update the document in
place instead of replacing it. Collections indexed before this contract store
the old flat keys — drop the collection once and re-run `alvis run` to
rebuild under the versioned schema.

#### Incremental ingestion

Idempotency avoids duplicates; `--incremental` also avoids *work*. Run with
`--incremental` and unchanged documents are skipped end-to-end (no extract /
chunk / embed / upsert), reporting the delta:

```text
$ alvis run pipeline.yaml --incremental
pipeline.yaml: 12 documents, 1 chunks indexed
  incremental: 1 changed, 11 skipped, 0 deleted
```

State lives in `.alvis/state.json` (`--state` to relocate) and is keyed by
source identity **and** a pipeline signature — changing extract/chunk/embed
settings invalidates it, so chunks are never silently left stale. State is
committed only after a successful run, and a failed run leaves the previous
state intact. Same toggle programmatically:

```python
run("pipeline.yaml", incremental=True)                    # or state_path="..."
await run_async(cfg, incremental=True, state_path=".alvis/state.json")
```

Phase 1 skips *processing*; Phase 2 skips *downloading* too. Connectors
fingerprint documents from listing data alone — S3 ETag (size fallback),
GitLab/GitHub blob sha, Confluence version — so unchanged remote objects are
never fetched; the filesystem source hashes files locally. A second run of an
unchanged 12-document source therefore does zero extraction, chunking, and
embedding.

**Change-triggered ingestion.** Point `--watch` at the configs and Alvis
polls them every `--interval` seconds, ingesting only what changed per tick —
the polling trigger is a scheduler primitive, so a push hook or cron only has
to invoke the (already-cheap) incremental run:

```text
$ alvis run pipeline.yaml --watch --interval 60
--watch implies --incremental; enabling incremental mode.
[14:02:11] pipeline.yaml: 12 documents, 0 chunks indexed
  incremental: 0 changed, 12 skipped, 0 deleted
```

The same loop is available programmatically as an async iterator
(`alvis.watch_async(configs, interval=..., state_path=...)`) — each yielded
tick is the per-config `PipelineResult`s, failures included, so a transient
source outage doesn't kill the watcher.

Transient failures are retried (429/5xx/connection issues) with exponential
backoff and jitter; `Retry-After` is honored. Per-source/index config accepts
`retries`, `retry_backoff`, and `verify: false` for self-signed HTTPS (use
the latter only against trusted internal endpoints).

## Documentation

- [Getting started](docs/getting-started.md) — install, first pipeline, first index
- [Docs index](docs/) — full docs site (getting started, FAQ, DSL, schema)
- [FAQ](docs/faq.md) — short answers to common questions
- [Contributing](CONTRIBUTING.md) — how to write a connector (cover page: connector fixture + golden tests)
- [Plugin registry](docs/plugins.md) — built-in and community plugins (v1.1 SDK)
- [Observability](docs/observability.md) — structured logging, metrics, tracing, Prometheus export
- [Operations](docs/status.md) — `alvis status`, metrics endpoint, `.env` loading, `init` templates, dashboards
- [Versioning & deprecation](docs/versioning.md) — semver, stable surface, deprecation window
- [Python DSL guide](docs/dsl.md) — describing pipelines from code
- [Constitution](CONSTITUTION.md) — purpose, scope, open-source strategy
- [Roadmap](ROADMAP.md) — build plan

## License

Apache 2.0. See [LICENSE](LICENSE).