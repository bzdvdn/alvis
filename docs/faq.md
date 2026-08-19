# FAQ

## What is Winnow?

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
documents per source identity. Add `winnow run --incremental` to skip
unchanged documents end-to-end (no re-extract/chunk/embed/upsert) — the
delta is reported and state is stored per source + pipeline signature.
Enable the [embedding cache](../README.md#embedding-cache) to skip
re-embedding unchanged chunks.

## How do I ingest on every change (not just manually)?

Run the incremental pipeline repeatedly — `winnow run --watch --interval 60`
polls the sources every 60 s and ingests only what changed (cheap listing
means unchanged remote objects aren't even downloaded). Same loop
programmatically with `winnow.watch_async(configs, interval=...,
state_path=...)`, an async iterator of per-tick results. For instant
reaction, keep `--watch` as the primitive a git post-push hook or a scheduler
cron calls.

## Can I add my own source without editing Winnow?

Yes — the Plugin SDK (v1.1). Write an installable package that registers under
the `winnow.plugins` entry-point group and declares a `winnow.plugin.Plugin`
(module docstring of `winnow.plugin` documents the factory contract; the
reference is `examples/kb-plugin`). After `pip install`, `winnow plugins`
lists it and `winnow run`/`validate` accept its type string — no fork, no core
change.

## Which index should I pick?

- `memory` — tests and prototypes.
- `qdrant` — the default for real use (`url`, `collection`).
- `pgvector` — PostgreSQL + pgvector (`winnow[pgindex]`).

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
`winnow query --answer` falls back to numbered excerpts instead of failing:
`answer(cfg, question, llm=Synthesizer(...))` / CLI `--answer`.

## Where do I report a bug or request a source?

Open an issue on the repository. When contributing a connector, follow the
process in [CONTRIBUTING.md](../CONTRIBUTING.md) — a connector lands only with
its test fixture and golden test.