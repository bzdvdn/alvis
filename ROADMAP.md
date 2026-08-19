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

- [ ] Plugin SDK: source / parser / chunker / embedder / indexer interfaces
- [ ] Plugin packaging: separate installable extension packages
- [ ] Manifest + discovery: plugins register via entry points
- [ ] Community connector registry (list + status + maintainers)

**Done when:** a side project adds a connector as a separate PyPI package with zero core changes.

## v2.0 — Ecosystem

Goal: Winnow maintains itself; the community drives breadth.

- [ ] Stable plugin API (semver, deprecation window)
- [ ] Managed/hosted offering evaluation (separate distribution, never a fork)
- [ ] Production hardening: observability hooks (logging, metrics, traces)
- [ ] Performance budget: p99 chunk+embed throughput targets

---

## Cross-cutting rules

- Every plugin lands only with: hello-world example + test fixture + CI golden test.
- Any change to the YAML contract or Canonical Content Tree is an RFC-first change.
- No feature ships without the chapter it documents.