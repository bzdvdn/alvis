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
- [ ] Confluence source adapter (minimal, read-only) — implemented, needs live test
- [ ] Qdrant index adapter (minimal, upsert) — implemented, mock-tested
- [x] Hello-world example in `examples/` + e2e smoke test (fs → memory)
- [ ] CI: lint (ruff), typecheck (mypy), tests (pytest)

**Done when:** `pip install -e . && winnow init && winnow run --config examples/confluence.yaml` produces a non-empty Qdrant collection, end-to-end, with zero manual Python.

## v0.2 — Dogfooding & Community

Goal: attract first contributors and prove the plugin contract.

- [ ] Docs site (README → docs/: getting started, FAQ)
- [ ] `CONTRIBUTING.md`: "how to write a connector" as the cover page
- [ ] Connector fixture + golden-test requirements enforced in CI
- [ ] Format parsers: Markdown, HTML (from Confluence export)
- [ ] CLI polish: structured output, exit codes, `--dry-run`
- [ ] First external connector PRs (pgvector backend or GitLab source)

**Done when:** at least one external contribution merged; a non-core-team member delivers a working connector feature.

## v1.0 — Stable Contract

Goal: freeze the public contract — Canonical Content Tree v1 + YAML schema v1.

- [ ] Canonical Content Tree v1 (versioned, backward-compatible evolution policy)
- [ ] Chunking strategies: auto (token), by heading, by size; overlap support
- [ ] Memory-safe handling of large PDF/DOCX/XLSX (async + streaming)
- [ ] Idempotent ingestion: per-source state, dedup by content hash, retries
- [ ] Sources: GitLab, S3
- [ ] Indexes: PostgreSQL/pgvector
- [ ] Embedding abstraction: model-agnostic, batching + caching
- [ ] CLI: config validation reports, pipeline dry-run graph

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