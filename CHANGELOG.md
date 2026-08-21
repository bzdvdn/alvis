# Changelog

All notable changes to Alvis are documented here.

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
