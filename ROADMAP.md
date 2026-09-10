# Alvis — Development Roadmap

This roadmap is the operating plan derived from the project constitution (`CONSTITUTION.md`). It tracks what we build, in what order, and how we verify it.

> Status legend: 🔲 planned · 🔧 in progress · ✅ done

---

## v0.1 — Foundation

Goal: a runnable skeleton with a real hello-world path (Confluence → Qdrant) driven entirely by YAML.

- [x] Project skeleton: `src/` layout, `pyproject.toml`, entry point
- [x] Repo bootstrap: git, `.gitignore`, LICENSE (Apache 2.0)
- [x] CLI surface: `alvis init`, `alvis run`, `alvis validate`
- [x] YAML schema + validation for the pipeline config (pydantic)
- [x] Config loader (`pipeline:` grammar per constitution example)
- [x] Canonical Content Tree core model (v0, provisional; pydantic)
- [x] Stage interfaces: extract / chunk / embed / index (async protocols)
- [x] Confluence source adapter (minimal, read-only) — live-verified via mock Confluence
- [x] Qdrant index adapter (minimal, upsert) — live-verified, collection created + searchable
- [x] Hello-world example in `examples/` + e2e smoke test (fs → memory)
- [x] CI: lint (ruff), typecheck (mypy), tests (pytest) — GitHub Actions
- [x] docker-compose dev environment (mock Confluence + real Qdrant); e2e verified
- [x] MinIO in docker-compose (seeded `alvis` bucket) — live-verified S3 source
- [x] Programmatic API (`alvis.run/run_async/run_many/run_many_async`) + CLI multi-config parallel runs (`--parallel`)
- [x] Typed Python DSL (`alvis.dsl`) — same config contract as YAML, parity tested
- [x] OpenAI-compatible embedder (`openai`) with batching + retries — live path verified against Qdrant dims
- [x] Format parsers: PDF/DOCX/XLSX (`alvis[documents]` extra, lazy deps) — live-verified DOCX e2e into Qdrant

**Done when:** `pip install -e . && alvis init && alvis run --config examples/confluence.yaml` produces a non-empty Qdrant collection, end-to-end, with zero manual Python. (Met: see examples/confluence-qdrant.yaml + docker-compose.)

**Done when:** `pip install -e . && alvis init && alvis run --config examples/confluence.yaml` produces a non-empty Qdrant collection, end-to-end, with zero manual Python.

## v0.2 — Dogfooding & Community

Goal: attract first contributors and prove the plugin contract.

- [x] Docs site (README → docs/: getting started, FAQ)
- [x] `CONTRIBUTING.md`: "how to write a connector" as the cover page
- [x] Connector fixture + golden-test requirements enforced in CI
- [x] Format parsers: Markdown, HTML (from Confluence export); PDF/DOCX/XLSX (done in v0.1 via `alvis[documents]`)
- [x] CLI polish: structured output, exit codes, `--dry-run` (multi-config `run --parallel`)
- [ ] First external connector PRs (pgvector backend or GitLab source)

**Done when:** at least one external contribution merged; a non-core-team member delivers a working connector feature.

## v0.3 — Q&A convenience (answering, not just retrieval)

Goal: go from "nearest chunks" to a cited answer, the workflow corporate users actually ask for.

- [x] Response synthesis with citations: `alvis.answer` (`Synthesizer` on the OpenAI-compatible chat contract; `[N]` markers → `Citation`s; offline `citation_answer` fallback so `--answer` works with no API key) + CLI `alvis query --answer`
- [x] Metadata filters + hybrid search (dense + BM25/full-text, Reciprocal Rank Fusion) — `Indexer.search(filters=...)` / `KeywordIndexer.keyword_search` implemented on every built-in backend (`memory`/`sqlite` BM25 locally, `pgvector` via `tsvector`/`ts_rank`, `qdrant` via a full-text payload index + local BM25 over the candidate pool); CLI `alvis query --filter key=value --hybrid`
- [x] Reranking over retrieved candidates — `alvis.rerank.LLMReranker` (LLM-based, not a local cross-encoder — no local ML model dependency by design, see CONSTITUTION); fails soft to the original order on any error; CLI `alvis query --rerank`
- [x] Multi-turn chat with history (follow-up questions) — `alvis.answer.ChatTurn`
  + `Synthesizer.answer(..., history=...)` replays prior turns as alternating
  user/assistant messages into the synthesis prompt; `answer`/`answer_async` gain
  `history`; CLI `alvis chat` is an interactive REPL keeping the session's
  transcript in memory. Retrieval itself is not history-aware (no query
  rewriting) — only synthesis sees the conversation.
- [x] Evaluation harness — `alvis.evaluation` (hit rate / MRR against a fixed case set) + CLI `alvis eval`; scores retrieval quality (did the right chunk come back), not yet answer quality (faithfulness/relevancy via an LLM judge) — that half is still open, see `docs/evaluation.md`'s Scope section

**Done when:** a user asks a natural-language question and gets a cited, grounded answer — with or without an LLM key.

## v0.4 — Incremental ingestion (load only what changed)

Goal: re-runs stop being full re-ingests. Content that didn't change is skipped end-to-end.

- [x] DocStore: per-source document state (uri → content fingerprint) keyed by pipeline signature (extract/chunk/embed settings), committed atomically only after a successful run
- [x] Skip unchanged documents in the engine (no extract/chunk/embed/upsert); reconcile still keeps their points; delta stats (`changed/skipped/deleted`) in `PipelineResult` + CLI `alvis run --incremental [--state]`
- [x] Phase 2 — cheap listing fingerprints for all sources: `DocumentMeta` + optional `ListingSource` interface (`list_documents()`, `fetch(uris=...)`); S3 ETag/size, GitLab/GitHub blob sha, Confluence version, fs content hash → unchanged remote objects are not downloaded at all
- [x] Change-triggered ingestion — polling trigger `alvis run --watch [--interval]` + programmatic `alvis.watch_async` (async iterator of per-tick results, soft-fail on transient outages); push hooks / schedulers just re-invoke the cheap incremental run

**Done when:** a second run of an unchanged corpus ingests zero chunks; a one-file edit ingests exactly that file.

## v1.0 — Stable Contract

Goal: freeze the public contract — Canonical Content Tree v1 + YAML schema v1.

- [x] Canonical Content Tree v1 (versioned YAML `schema_version` + `Document.schema_version`, backward-compatible evolution policy in docs/schema.md)
- [x] Chunking strategies: auto (token), by heading (`sections`), by size; overlap support
- [x] Memory-safe handling of large PDF/DOCX/XLSX (async + streaming): extract `max_bytes` cap streamed via `aiter_bytes` (413 skip in GitLab/S3/GitHub), fs size pre-check, XLSX `read_only` row streaming; compute in `to_thread`
- [x] Idempotent ingestion: deterministic point IDs (uuid5), per-source reconcile pass, dedup by content hash — live-verified (re-run 6→6, change/delete prune)
- [x] Retrieval: `alvis.query` / `query_async` (SearchHit with cosine score) + CLI `alvis query` — live-verified against pgvector (exact section match) and Qdrant (1.0 for verbatim chunk)
- [x] Retries in HttpClient: 429/5xx/network, exponential backoff + jitter, Retry-After; `retries`/`retry_backoff`/`verify` config — live-verified (survived Qdrant outage)
- [x] Sources: GitHub, GitLab (self-hosted via `url`), S3 (SigV4, no boto3; MinIO-compatible) — include/exclude globs + prefix/path scoping; live-verified against MinIO (videos excluded, idempotent re-run)
- [x] Indexes: PostgreSQL/pgvector (`alvis[pgindex]`, psycopg 3, idempotent upsert + reconcile)
- [x] Embedding abstraction: model-agnostic, batching + caching
- [x] CLI: config validation reports, pipeline dry-run graph

**Done when:** contract v1 frozen; migration path documented and tested.

## v1.1 — Plugin SDK

Goal: third parties can extend Alvis without touching the core.

- [x] Plugin SDK: source / extractor / chunker / embedder / indexer adapter kinds — a `Plugin` declaration + uniform factory contract (`alvis.plugin`); `check_pipeline_supported`, `validate`, stage factories, and `alvis plugins` all consult the registry, so a plugin type is accepted the moment it is discovered
- [x] Plugin packaging: separate installable extension packages — `examples/kb-plugin` (editable-installed and run end-to-end in CI-equivalent tests) proves the "side package, zero core changes" path
- [x] Manifest + discovery: plugins register via `alvis.plugins` entry points; discovery is lazy, idempotent, and broken packages are skipped with a warning
- [x] Community connector registry — `docs/plugins.md` (built-in table + published-plugin rows) + `alvis plugins` CLI listing

**Done when:** a side project adds a connector as a separate PyPI package with zero core changes. (Infrastructure shipped; an external contribution is the remaining proof.)

## v2.0 — Ecosystem

Goal: Alvis maintains itself; the community drives breadth.

- [x] Stable plugin API (semver, deprecation window) — [docs/versioning.md](docs/versioning.md) fixes the package semver + stable surface (plugin SDK, config, CCT, engine/result, adapter protocols) at `1.0.0`; `alvis.deprecated` provides the loud, once-per-callsite warning cycle with a named removal version; breaking a stable name requires a MAJOR and a warning cycle (see CONTRIBUTING cross-cutting rules)
- [x] Deployable container — multi-stage `Dockerfile` (slim base, all extras, non-root `alvis` user, entrypoint `alvis`) ships the product as an image driven purely by mounted YAML; built and smoke-verified (fs → memory, cross-container incremental state)
- [ ] Managed/hosted offering evaluation (separate distribution, never a fork)
- [x] Observability hooks — structured logging (JSON/text via `setup_logging`), in-process metrics with optional Prometheus backing (`alvis[observability]`), optional OpenTelemetry spans, `--log-json`/`--log-level` on the CLI
- [x] Pipeline stage decomposition — orchestration split out of the engine monolith into `alvis.pipeline.stages` (Fetch/Extract/Embed/Upsert/Reconcile/Commit) sharing a `RunContext`, each independently testable and timed as its own `pipeline_stage_seconds stage=<name>` sample; no behavior change (all existing tests + golden green)
- [x] Project layout defaults — `alvis run` with no paths scans `alvis/pipelines/*.yaml` (+ `./alvis.yaml`) and runs them concurrently; local companion plugins load via an explicit `--plugins <dir>` flag (`alvis.plugin.load_local_plugins`), never implicitly
- [x] Production hardening — version 0.6.0; CONSTITUTION aligned to reality (Python 3.10+, as CI/pyproject); import-cycle-free module layout (verified by dependency analyzer); plugin factory contract tests (keyword-only signatures); pytest coverage gate ≥90% + `pip-audit` job in CI
- [ ] Performance budget: p99 chunk+embed throughput targets

## v2.1 — Retrieval hardening & production readiness

Goal: close the gaps a senior-architect review found in the shipped v2.0 contract —
retrieval quality, multi-tenancy, connection/fan-out hygiene, and secrets, none of
which needed a contract-breaking change.

- [x] ACL-aware retrieval — `source.config.acl` (static per-source principal list) →
  reserved `__acl` field → `Indexer.search`/`KeywordIndexer.keyword_search(principals=...)`,
  enforced server-side on `qdrant`/`pgvector`, in Python on `memory`/`sqlite`; CLI
  `alvis query --principal`. Honestly scoped as a static tag, not live per-document
  permissions from the origin system — no connector fetches those today.
- [x] Production-backend connection hardening — `HttpClient` reuses one pooled
  `httpx.AsyncClient` instead of one per request; `PgVectorIndex` reuses one
  connection instead of reconnecting per statement; `QdrantIndex`/`PgVectorIndex`
  batch upserts (`BatchIndexer` protocol) instead of one write per chunk.
- [x] Concurrency budget on embed/fetch fan-out — `ApiEmbedder.embed_batch` and every
  remote source's per-document fetch (`github`/`gitlab`/`s3`/`confluence`/
  `static_url`) dispatch concurrently, bounded by `max_concurrency`, instead of one
  request at a time.
- [x] Secrets backend — `alvis.secrets.SecretResolver`: env vars by default (no
  behavior change), swappable to HashiCorp Vault (KV v2 over plain HTTP, no `hvac`
  dependency) via `ALVIS_SECRETS_BACKEND=vault`, or a custom resolver for AWS
  Secrets Manager etc.
- [x] `cli.py` split into a package (`alvis/cli/`, one module per subcommand) — was
  1143 lines in one file, now under ~240 lines per file.
- [x] DSL/YAML parity audit — `alvis.dsl` builders were missing `max_bytes` and the
  new `max_concurrency` knobs on several source/embedder builders (YAML always
  supported them via the raw config dict; the typed builders had to catch up by hand).
- [x] Answer-quality evaluation (faithfulness/relevancy via an LLM judge) —
  `alvis.judge.AnswerJudge` scores a synthesized answer against its excerpts on
  both axes; `evaluate`/`evaluate_async` accept `llm`+`judge` to opt in (default
  stays retrieval-only, no API key needed); CLI `alvis eval --judge`
  (`--answer-*` for the synthesis model, `--judge-*` for the grading model — can
  differ, e.g. a stronger judge grading a cheaper model's answers).
- [x] Vector-store breadth, first step: `elasticsearch` (Elasticsearch 8.0+'s
  native `dense_vector`/`knn` search specifically, not OpenSearch's separate
  k-NN plugin dialect — no client library, plain REST like every other
  backend). Its `keyword_search` runs Elasticsearch's real server-side BM25,
  unlike `qdrant`'s local-scoring-over-a-candidate-pool workaround. No
  batch-write path yet (`_bulk` needs a raw NDJSON body the shared
  `HttpClient` doesn't support) — falls back to the per-chunk upsert loop.
  Weaviate/Pinecone/OpenSearch's own dialect remain open; the Plugin SDK
  makes any of them possible externally without a core change.
- [ ] Source connectors beyond the DevOps/engineering-KB profile (GitLab/GitHub/
  Confluence/S3/fs/static_url) — no Notion/SharePoint/Google Drive/Slack/Jira.

---

## Cross-cutting rules

- Every plugin lands only with: hello-world example + test fixture + CI golden test.
- Any change to the YAML contract or Canonical Content Tree is an RFC-first change.
- No feature ships without the chapter it documents.