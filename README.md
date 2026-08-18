# Winnow

No-code knowledge ingestion engine. Build corporate knowledge bases from heterogeneous sources (GitLab, Confluence, S3; Markdown, HTML, PDF, DOCX, XLSX, CSV) without writing Python.

```
Source → Artifact → Extraction → Canonical Content Tree → Chunking → Embedding → Index
```

## Status

Alpha (v0.1.0). Skeleton only — CLI surface, config model, stage contracts.

## Quick start

```bash
pip install -e .
winnow --version
winnow init
winnow validate examples/pipeline.yaml
```

## Documentation

- [Constitution](CONSTITUTION.md) — purpose, scope, open-source strategy
- [Roadmap](ROADMAP.md) — build plan
- `docs/` — guides (getting started, contributor guide, in progress)

## License

Apache 2.0. See [LICENSE](LICENSE).