# Winnow — Knowledge Ingestion Engine

**Project Constitution**

**Version:** 1.2.0  
**Status:** Architecture baseline  
**Language:** Python 3.12+  
**Primary backends:** Qdrant, PostgreSQL/pgvector  
**Primary sources:** GitLab, Confluence, S3  
**Primary formats:** Markdown, HTML, PDF, DOCX, XLSX, CSV, source code

---

# 1. Purpose

Winnow is an internal framework for building corporate knowledge bases from heterogeneous sources.

The core pipeline:

    Source
      ↓
    Artifact
      ↓
    Extraction
      ↓
    Canonical Content Tree
      ↓
    Transformation
      ↓
    Chunking
      ↓
    Embedding
      ↓
    Index

The framework must allow users to assemble an ingestion pipeline declaratively,
without writing Python code — a YAML descriptor must be sufficient for any
pipeline (no-code by default). The typed Python DSL (`winnow.dsl`) is a
first-class alternative that produces the exact same config contract, and is
verified to run identically to its YAML twin.

Example:

```yaml
pipeline:
  source:
    type: confluence

  extract:
    strategy: auto

  chunk:
    strategy: auto
    config:
      max_tokens: 500
      overlap: 50

  embed:
    type: default

  index:
    type: qdrant
```

# 2. Scope

## In scope
- Pluggable source adapters (GitLab, Confluence, S3) and format parsers (Markdown, HTML, PDF, DOCX, XLSX, CSV, source code).
- Configurable extraction, chunking, and transformation strategies exposed via YAML.
- Canonical Content Tree as the intermediate representation between extraction and indexing.
- Embedding and indexing into a pluggable vector store.
- Idempotent, deduplicated ingestion with retry and error-tolerance semantics.

## Out of scope
- Building end-user applications (chatbots, search UIs) on top of the indexed data.
- Managing source-of-truth systems (GitLab, Confluence, S3) themselves.
- Embedding model training.

# 3. Open Source Strategy

Winnow is distributed and developed as an open-source project.

## Principles
- **Framework, not app.** The core must stay source/format-agnostic; all integration lives behind pluggable adapters.
- **Declarative by default.** Users assemble pipelines via YAML; the typed Python DSL is a first-class alternative with the same contract. Python remains the language for plugin SDK extensions.
- **First impression wins.** Time-to-hello-world is a hard quality gate: `pip install winnow` + one YAML must produce a running index.

## License
- **Apache 2.0.** Permissive for corporate adopters, allows proprietary integration without forcing contribution back.
- CLA required for external contributors to keep the licensing path clean.
- A separate managed/hosted offering (future, optional) is a distinct distribution, never a fork of the core.

## Repository & governance
- Single main repo (`winnow/winnow`) for core + CLI.
- Adapters/bundles may live in the same repo under `plugins/` until they prove stability, then graduate to the mono-repo or their own repos as the community demands.
- `CONTRIBUTING.md` contract: "how to write a connector" is the cover-page of the contributor guide, not an appendix.
- Discussions/issues on GitHub; RFCs for anything that changes the YAML contract or Canonical Content Tree.

## Contributor model
- Core team: pipeline core, Canonical Content Tree, embedding/index abstraction.
- Community: connectors, format parsers, chunking strategies.
- Review rule: any plugin lands only with a hello-world example + test fixture + CI golden test.

# 4. Roadmap

- v0.1 — CLI (`winnow init/run/validate`), YAML schema validation, hello-world on Confluence → Qdrant
- v0.2 — docs site, contributor guide, first external connector PRs
- v1.0 — core pipeline stable contract (Canonical Content Tree v1), pgvector + GitLab/S3 sources
- v1.1 — plugin SDK for third-party source/strategy extensions
- v2.0 — stable plugin API, community-managed connector registry
