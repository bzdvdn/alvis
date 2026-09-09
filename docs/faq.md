# FAQ

## What is Alvis?

A no-code knowledge ingestion and retrieval engine. It turns heterogeneous
sources (GitLab, Confluence, S3, local files) and formats (Markdown, HTML,
PDF, DOCX, XLSX, CSV) into a searchable vector index, driven entirely by a
YAML pipeline descriptor — no Python required.

## What stages does a pipeline have?

`source → extract → chunk → embed → index`. YAML describes them
declaratively; the engine builds the adapters. Only `source` is required;
missing stages use defaults.

## Which sources are built in?

`fs`, `confluence`, `github`, `gitlab` (self-hosted via `url`), and `s3`
(SigV4, no boto3, MinIO-compatible). See the README source table for config
keys. New sources are connectors — see the
[contributing guide](../CONTRIBUTING.md).

## Do I need a GPU or an API key to try it?

No. `embed.type: default` is a deterministic local placeholder. Add
`embed.type: openai` (any OpenAI-compatible endpoint) when you want real
vectors.

## How do I make re-runs idempotent?

They already are. Chunk point IDs are deterministic, identical content is
overwritten, and a `reconcile` pass prunes points of changed or deleted
documents per source identity. Add `alvis run --incremental` to skip
unchanged documents end-to-end (no re-extract/chunk/embed/upsert) — the
delta is reported and state is stored per source + pipeline signature.
Enable the [embedding cache](../README.md#embedding-cache) to skip
re-embedding unchanged chunks.

## How do I ingest on every change (not just manually)?

Run the incremental pipeline repeatedly — `alvis run --watch --interval 60`
polls the sources every 60 s and ingests only what changed (cheap listing
means unchanged remote objects aren't even downloaded). Same loop
programmatically with `alvis.watch_async(configs, interval=...,
state_path=...)`, an async iterator of per-tick results. For instant
reaction, keep `--watch` as the primitive a git post-push hook or a scheduler
cron calls.

## Can I add my own source without editing Alvis?

Yes — the Plugin SDK (v1.1). Write an installable package that registers under
the `alvis.plugins` entry-point group and declares a `alvis.plugin.Plugin`
(module docstring of `alvis.plugin` documents the factory contract; the
reference is `examples/kb-plugin`). After `pip install`, `alvis plugins`
lists it and `alvis run`/`validate` accept its type string — no fork, no core
change.

## How do I monitor what ingestion is doing?

Structured logs, metrics, and (optionally) traces — see
[docs/observability.md](observability.md). `alvis run --log-json --log-level
info pipeline.yaml` renders every per-run event as one JSON line; the
process-wide metrics store counts runs/documents/chunks plus latency
histograms per stage, exportable as Prometheus text with
`alvis[observability]` installed. No extra dependency is required for the
basic in-process store.

## Which index should I pick?

- `memory` — tests and prototypes.
- `qdrant` — the default for real use (`url`, `collection`).
- `pgvector` — PostgreSQL + pgvector (`alvis[pgindex]`).

## The embedder is deterministic. Is that real?

For dev/test, yes by design. For production vectors use `openai` (compatible
with OpenAI, Azure, or a local server like Ollama). The abstraction is
model-agnostic, so switching requires an embedder that implements the same
`Embedder` protocol.

## Can I suppress retries or verify TLS?

Each source/index accepts `retries`, `retry_backoff`, and `verify`. Use
`verify: false` only against trusted internal endpoints with self-signed
certificates.

## Can it answer questions, or only retrieve chunks?

Both. Retrieval returns the nearest chunks (`SearchHit`); for a grounded
answer add an OpenAI-compatible chat endpoint and the answer is synthesized
with `[N]` citations tied to each `source_uri`. Without an API key,
`alvis query --answer` falls back to numbered excerpts instead of failing:
`answer(cfg, question, llm=Synthesizer(...))` / CLI `--answer`.

## Can I restrict who sees what in retrieval?

Yes, at the source level: `source.config.acl: [principal, ...]` stamps a
static list of principal strings onto every chunk that source produces
(promoted to the reserved `__acl` field). `alvis query --principal name`
(repeatable) then excludes chunks whose `acl` doesn't include any of the
given principals — a chunk with no `acl` is always public. Omit
`--principal` for no filtering at all (the default). This is a static
per-source tag, not live per-document permissions pulled from the origin
system — no connector fetches those today. See the
[README's ACL section](../README.md#acl-aware-retrieval).

## Where do I report a bug or request a source?

Open an issue on the repository. When contributing a connector, follow the
process in [CONTRIBUTING.md](../CONTRIBUTING.md) — a connector lands only with
its test fixture and golden test.