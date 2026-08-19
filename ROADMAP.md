# Winnow — Development Roadmap

This roadmap is the operating plan derived from the project constitution (`CONSTITUTION.md`). It tracks what we build, in what order, and how we verify it.

> Status legend: 🔲 planned · 🔧 in progress · ✅ done

---

## v0.1 — Foundation

Goal: a runnable skeleton with a real hello-world path (Confluence → Qdrant) driven entirely by YAML.

- [x] Project skeleton: `src/` layout, `pyproject.toml`, entry point
- [x] Repo bootstrap: git, `.gitignore`, LICENSE (Apache 2.0)
- [x] CLI surface: `winnow init`, `winnow run`, `winnow validate`
- [x] YAML schema + validation for the pipeline config (pydantic)
- [x] Config loader (`pipeline:` grammar per constitution example)
- [x] Canonical Content Tree core model (v0, provisional; pydantic)
- [x] Stage interfaces: extract / chunk / embed / index (async protocols)
- [x] Confluence source adapter (minimal, read-only) — live-verified via mock Confluence
- [x] Qdrant index adapter (minimal, upsert) — live-verified, collection created + searchable
- [x] Hello-world example in `examples/` + e2e smoke test (fs → memory)
- [x] CI: lint (ruff), typecheck (mypy), tests (pytest) — GitHub Actions
- [x] docker-compose dev environment (mock Confluence + real Qdrant); e2e verified
- [x] MinIO in docker-compose (seeded `winnow` bucket) — live-verified S3 source
- [x] Programmatic API (`winnow.run/run_async/run_many/run_many_async`) + CLI multi-config parallel runs (`--parallel`)
- [x] Typed Python DSL (`winnow.dsl`) — same config contract as YAML, parity tested
- [x] OpenAI-compatible embedder (`openai`) with batching + retries — live path verified against Qdrant dims
- [x] Format parsers: PDF/DOCX/XLSX (`winnow[documents]` extra, lazy deps) — live-verified DOCX e2e into Qdrant

**Done when:** `pip install -e . && winnow init && winnow run --config examples/confluence.yaml` produces a non-empty Qdrant collection, end-to-end, with zero manual Python. (Met: see examples/confluence-qdrant.yaml + docker-compose.)

**Done when:** `pip install -e . && winnow init && winnow run --config examples/confluence.yaml` produces a non-empty Qdrant collection, end-to-end, with zero manual Python.

## v0.2 — Dogfooding & Community

Goal: attract first contributors and prove the plugin contract.

- [x] Docs site (README → docs/: getting started, FAQ)
- [x] `CONTRIBUTING.md`: "how to write a connector" as the cover page
- [x] Connector fixture + golden-test requirements enforced in CI
- [x] Format parsers: Markdown, HTML (from Confluence export); PDF/DOCX/XLSX (done in v0.1 via `winnow[documents]`)
- [x] CLI polish: structured output, exit codes, `--dry-run` (multi-config `run --parallel`)
- [ ] First external connector PRs (pgvector backend or GitLab source)

**Done when:** at least one external contribution merged; a non-core-team member delivers a working connector feature.

## v0.3 — Q&A convenience (answering, not just retrieval)

Goal: go from "nearest chunks" to a cited answer, the workflow corporate users actually ask for.

- [x] Response synthesis with citations: `winnow.answer` (`Synthesizer` on the OpenAI-compatible chat contract; `[N]` markers → `Citation`s; offline `citation_answer` fallback so `--answer` works with no API key) + CLI `winnow query --answer`
- [ ] Metadata filters + hybrid search (dense + BM25/full-text) — filter by `space:`/`path:`/source before scoring
- [ ] Reranking (cross-encoder) over retrieved candidates
- [ ] Multi-turn chat with history (follow-up questions)
- [ ] Evaluation harness (faithfulness/relevancy) to prove retrieval quality

**Done when:** a user asks a natural-language question and gets a cited, grounded answer — with or without an LLM key.

## v0.4 — Incremental ingestion (load only what changed)

Goal: re-runs stop being full re-ingests. Content that didn't change is skipped end-to-end.

- [x] DocStore: per-source document state (uri → content fingerprint) keyed by pipeline signature (extract/chunk/embed settings), committed atomically only after a successful run
- [x] Skip unchanged documents in the engine (no extract/chunk/embed/upsert); reconcile still keeps their points; delta stats (`changed/skipped/deleted`) in `PipelineResult` + CLI `winnow run --incremental [--state]`
- [x] Phase 2 — cheap listing fingerprints for all sources: `DocumentMeta` + optional `ListingSource` interface (`list_documents()`, `fetch(uris=...)`); S3 ETag/size, GitLab/GitHub blob sha, Confluence version, fs content hash → unchanged remote objects are not downloaded at all
- [x] Change-triggered ingestion — polling trigger `winnow run --watch [--interval]` + programmatic `winnow.watch_async` (async iterator of per-tick results, soft-fail on transient outages); push hooks / schedulers just re-invoke the cheap incremental run

**Done when:** a second run of an unchanged corpus ingests zero chunks; a one-file edit ingests exactly that file.

## v1.0 — Stable Contract

Goal: freeze the public contract — Canonical Content Tree v1 + YAML schema v1.

- [x] Canonical Content Tree v1 (versioned YAML `schema_version` + `Document.schema_version`, backward-compatible evolution policy in docs/schema.md)
- [x] Chunking strategies: auto (token), by heading (`sections`), by size; overlap support
- [x] Memory-safe handling of large PDF/DOCX/XLSX (async + streaming): extract `max_bytes` cap streamed via `aiter_bytes` (413 skip in GitLab/S3/GitHub), fs size pre-check, XLSX `read_only` row streaming; compute in `to_thread`
- [x] Idempotent ingestion: deterministic point IDs (uuid5), per-source reconcile pass, dedup by content hash — live-verified (re-run 6→6, change/delete prune)
- [x] Retrieval: `winnow.query` / `query_async` (SearchHit with cosine score) + CLI `winnow query` — live-verified against pgvector (exact section match) and Qdrant (1.0 for verbatim chunk)
- [x] Retries in HttpClient: 429/5xx/network, exponential backoff + jitter, Retry-After; `retries`/`retry_backoff`/`verify` config — live-verified (survived Qdrant outage)
- [x] Sources: GitHub, GitLab (self-hosted via `url`), S3 (SigV4, no boto3; MinIO-compatible) — include/exclude globs + prefix/path scoping; live-verified against MinIO (videos excluded, idempotent re-run)
- [x] Indexes: PostgreSQL/pgvector (`winnow[pgindex]`, psycopg 3, idempotent upsert + reconcile)
- [x] Embedding abstraction: model-agnostic, batching + caching
- [x] CLI: config validation reports, pipeline dry-run graph

**Done when:** contract v1 frozen; migration path documented and tested.

## v1.1 — Plugin SDK

Goal: third parties can extend Winnow without touching the core.

- [x] Plugin SDK: source / extractor / chunker / embedder / indexer adapter kinds — a `Plugin` declaration + uniform factory contract (`winnow.plugin`); `check_pipeline_supported`, `validate`, stage factories, and `winnow plugins` all consult the registry, so a plugin type is accepted the moment it is discovered
- [x] Plugin packaging: separate installable extension packages — `examples/kb-plugin` (editable-installed and run end-to-end in CI-equivalent tests) proves the "side package, zero core changes" path
- [x] Manifest + discovery: plugins register via `winnow.plugins` entry points; discovery is lazy, idempotent, and broken packages are skipped with a warning
- [x] Community connector registry — `docs/plugins.md` (built-in table + published-plugin rows) + `winnow plugins` CLI listing

**Done when:** a side project adds a connector as a separate PyPI package with zero core changes. (Infrastructure shipped; an external contribution is the remaining proof.)

## v2.0 — Ecosystem

Goal: Winnow maintains itself; the community drives breadth.

- [x] Stable plugin API (semver, deprecation window) — [docs/versioning.md](docs/versioning.md) fixes the package semver + stable surface (plugin SDK, config, CCT, engine/result, adapter protocols) at `1.0.0`; `winnow.deprecated` provides the loud, once-per-callsite warning cycle with a named removal version; breaking a stable name requires a MAJOR and a warning cycle (see CONTRIBUTING cross-cutting rules)
- [x] Deployable container — multi-stage `Dockerfile` (slim base, all extras, non-root `winnow` user, entrypoint `winnow`) ships the product as an image driven purely by mounted YAML; built and smoke-verified (fs → memory, cross-container incremental state)
- [ ] Managed/hosted offering evaluation (separate distribution, never a fork)
- [x] Observability hooks — structured logging (JSON/text via `setup_logging`), in-process metrics with optional Prometheus backing (`winnow[observability]`), optional OpenTelemetry spans, `--log-json`/`--log-level` on the CLI
- [x] Pipeline stage decomposition — orchestration split out of the engine monolith into `winnow.pipeline.stages` (Fetch/Extract/Embed/Upsert/Reconcile/Commit) sharing a `RunContext`, each independently testable and timed as its own `pipeline_stage_seconds stage=<name>` sample; no behavior change (all existing tests + golden green)
- [x] Project layout defaults — `winnow run` with no paths scans `winnow/pipelines/*.yaml` (+ `./winnow.yaml`) and runs them concurrently; local companion plugins load via an explicit `--plugins <dir>` flag (`winnow.plugin.load_local_plugins`), never implicitly
- [x] Production hardening — version 0.6.0; CONSTITUTION aligned to reality (Python 3.10+, as CI/pyproject); import-cycle-free module layout (verified by dependency analyzer); plugin factory contract tests (keyword-only signatures); pytest coverage gate ≥90% + `pip-audit` job in CI
- [ ] Performance budget: p99 chunk+embed throughput targets

---

## Cross-cutting rules

- Every plugin lands only with: hello-world example + test fixture + CI golden test.
- Any change to the YAML contract or Canonical Content Tree is an RFC-first change.
- No feature ships without the chapter it documents.