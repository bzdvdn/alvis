# Changelog

All notable changes to Alvis are documented here.

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
