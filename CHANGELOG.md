# Changelog

All notable changes to Alvis are documented here.

## [1.0.0rc3] - 2026-09-10

Retrieval quality, multi-tenancy, production hardening, and four new source
connectors — the bulk of the post-rc2 roadmap (`ROADMAP.md` v2.1).

### Added

- **Hybrid search on every index backend.** `query`/`eval` gain `--hybrid`:
  fuses dense search with a keyword ranking via Reciprocal Rank Fusion.
  `memory`/`sqlite` BM25 the corpus locally; `pgvector` ranks natively via
  `tsvector`/`ts_rank`; `qdrant` BM25-scores a server-narrowed full-text
  candidate pool. A backend without any keyword search (a plugin) degrades
  to dense-only, logged not errored.
- **LLM-based reranking.** `alvis.rerank.LLMReranker` reorders retrieved
  candidates via an OpenAI-compatible chat call; fails soft to the original
  order on any error. CLI `alvis query --rerank` / `alvis eval --rerank`.
- **Retrieval-quality evaluation harness.** `alvis.evaluation` (`EvalCase`,
  `evaluate`/`evaluate_async`) scores hit rate/MRR against a fixed case set,
  no LLM required. CLI `alvis eval <config> <cases.yaml> [--min-hit-rate]
  [--json]`.
- **Answer-quality (LLM-judge) evaluation.** `alvis.judge.AnswerJudge`
  scores a synthesized answer for faithfulness/relevancy against its
  excerpts. `evaluate`/`evaluate_async` accept `llm`+`judge`; CLI
  `alvis eval --judge` (separate `--answer-*`/`--judge-*` model config).
- **Multi-turn chat.** `alvis.answer.ChatTurn` + `Synthesizer.answer(...,
  history=...)` replay prior turns into the synthesis prompt.
  `answer`/`answer_async` gain `history`. New CLI `alvis chat <config>` —
  an interactive REPL keeping the session's transcript in memory.
- **ACL-aware retrieval.** `source.config.acl` (a static per-source
  principal list) stamps the reserved `__acl` field onto every chunk;
  `Indexer.search`/`KeywordIndexer.keyword_search` gain `principals`,
  enforced server-side on `qdrant`/`pgvector`. CLI `alvis query --principal`
  / `alvis eval --principal`. A static per-source tag, not live
  per-document permissions from the origin system.
- **Metadata filters on retrieval.** `Indexer.search(filters=...)`; CLI
  `alvis query --filter key=value` (repeatable, all must match).
- **Pluggable secrets backend.** `alvis.secrets`: every `*_env` config key
  (`api_token_env`, `dsn_env`, ...) resolves through a swappable
  `SecretResolver` — env vars by default (no behavior change), or
  HashiCorp Vault (`ALVIS_SECRETS_BACKEND=vault`, KV v2 over plain HTTP,
  no extra dependency), or a custom resolver.
- **Concurrency budget on fan-out.** `ApiEmbedder.embed_batch` and every
  remote source's per-document fetch now dispatch concurrently, bounded by
  `max_concurrency` (embed default 4, sources default 8), instead of
  strictly one request at a time.
- **Production-backend connection hardening.** `HttpClient` reuses one
  pooled `httpx.AsyncClient` per instance instead of one per request;
  `PgVectorIndex` reuses one connection instead of reconnecting per
  statement; `QdrantIndex`/`PgVectorIndex` batch upserts (`BatchIndexer`
  protocol) instead of one write per chunk. `HttpClient` also gained a
  `form` request-body option (`application/x-www-form-urlencoded`), needed
  by the new OAuth2-based sources below.
- **`elasticsearch` index backend** — Elasticsearch 8.0+'s native
  `dense_vector`/`knn` search (not OpenSearch's separate k-NN dialect). No
  client-library dependency; `keyword_search` runs Elasticsearch's own
  server-side BM25.
- **Four new source connectors**, each with DSL parity from day one:
  - `notion` — pages shared with a Notion integration (integration-token
    auth), rendered to a lightweight Markdown approximation.
  - `jira` — issues of a project or JQL query (Jira Cloud, Basic auth,
    same scheme as `confluence`).
  - `sharepoint` — files in a site's document library via Microsoft Graph
    (OAuth2 client-credentials against Azure AD, no user in the loop).
  - `gdrive` — files a Google service account can see via Drive API v3
    (OAuth2 via a self-signed RS256 JWT; the one source needing a real
    third-party dependency, `pip install alvis[gdrive]`, for RSA signing).
- **`dsl.none()` / `type: none` source** — zero documents, always, for
  pipelines built only to `query`/`answer`/`evaluate` an already-ingested
  index (`PipelineConfig.source` is required, but those functions never
  read it). Replaces the previous throwaway-real-source idiom.
- `alvis/cli.py` (1143 lines) split into `alvis/cli/`, one module per
  subcommand.

### Fixed

- `factories.source_identity` collided for `gitlab` **group** sources
  (and, for the same reason, every source type added in this release):
  the identity didn't include enough config to distinguish two different
  instances of the same source type, so two pipelines sharing an index
  could reconcile-delete each other's points. Every source type's
  identity now includes whatever actually varies per instance.
- `GitLabSource` aborted an entire **group** listing when one member
  project had no commits on the configured branch (a 404) — now that
  project is skipped, not the whole group. An explicitly configured
  single `project` still raises on 404 (far more likely a real
  misconfiguration worth surfacing).
- `factories.build_source` raised "got multiple values for argument
  'max_bytes'" when a source's own YAML `config:` also declared
  `max_bytes` (the engine-level cap collided with the per-source one).
  The per-source value now wins and is forwarded only once.

## [1.0.0rc2] - 2026-08-21

New index backend and DSL/CLI surface for single-file persistence.

### Added

- `sqlite` index — a persistent single-file vector store with no extra
  dependencies, covering the dev/prototype use case previously reserved for a
  standalone chroma backend.
  - `dsl.sqlite(path="alvis.db")` builder and `type: sqlite` YAML config.
  - `alvis init --index sqlite` scaffolding with default `alvis.db` path.
  - `sqlite` registered as a known index in `KNOWN_INDEXES`.
- GitLab source now traverses **group** repositories, not just a single
  project.
  - New `group` config/DSL option — lists every project in the group
    (paginated) and ingests each one's tree. `project` stays for single-repo
    use; one of `project` / `group` is required.
  - New `project_include_globs` / `project_exclude_globs` to narrow which
    group repositories are ingested (matched against the full `group/project`
    path; exclude wins).
  - New `include_archived` flag (default `false`) to also traverse archived
    group projects.
  - Artifacts now carry a `project` metadata field (the source repository
    path).

## [1.0.0rc1] - 2026-08-20

First release candidate for the stable Alvis contract.

### Added

- Versioned YAML pipeline schema (`schema_version: 1`) and Canonical Content Tree v1.
- Typed Python DSL with parity to the YAML contract.
- Filesystem, Confluence, GitHub, GitLab, S3, and static URL sources.
- Memory, Qdrant, and optional pgvector indexes.
- Incremental ingestion, cheap remote listing fingerprints, reconcile, and watch mode.
- OpenAI-compatible embeddings with batching, retries, and optional caching.
- Retrieval APIs (`alvis.query`, `alvis.answer`) and CLI commands.
- Plugin SDK with discovery, validation, and a documented deprecation policy.
- Structured logging, metrics, optional Prometheus/OpenTelemetry support, and status reporting.
- Versioned Qdrant payload fields with reserved `__` system namespace and `__document_id`.
- Multi-platform Docker image build configuration for `bzdvdn/alvis`.

### Breaking Changes

- Product, import package, CLI, and distribution were renamed from `winnow` to `alvis`.
- The default incremental state directory is now `.alvis/`.
- The default CLI command is now `alvis`.

### Known Limitations

- Qdrant point identity and reconcile currently use source URI; document-id-ledger
  ownership is planned for a later release.
- Metadata filters, hybrid search, reranking, multi-turn history, and LLM-based
  evaluation remain v2.x scope.
- The `default` embedder is deterministic and intended for development/testing;
  production deployments should configure an OpenAI-compatible embedder.

## [0.6.0]

See the repository history for pre-Alvis development releases.
