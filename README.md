# Winnow

No-code knowledge ingestion engine. Build corporate knowledge bases from heterogeneous sources (GitLab, Confluence, S3; Markdown, HTML, PDF, DOCX, XLSX, CSV) without writing Python.

```
Source → Artifact → Extraction → Canonical Content Tree → Chunking → Embedding → Index
```

## Status

Alpha (v0.1.0). Core pipeline runs end-to-end: fs/Confluence sources → extract → chunk → embed → memory/Qdrant index. Async-native (httpx). The embedder (`default`) is a deterministic placeholder for v0.1; real models land in v1.0.

## Quick start

```bash
pip install -e .
winnow --version
winnow init                # scaffold winnow.yaml
winnow validate winnow.yaml
winnow run examples/hello-pipeline.yaml   # zero-dependency hello world
winnow run winnow.yaml --dry-run          # describe pipeline without running
```

## End-to-end with docker-compose

Runs the full pipeline against real services (real Qdrant, mock Confluence):

```bash
docker compose up -d --build
winnow run examples/confluence-qdrant.yaml
# verify: points in the collection
curl http://localhost:6333/collections/winnow_docs
docker compose down          # stop services
```

The mock Confluence implements the `/rest/api` contract our adapter consumes
(`docker/mock-confluence/`); swap its `url` for a real instance when ready.

## Documentation

- [Constitution](CONSTITUTION.md) — purpose, scope, open-source strategy
- [Roadmap](ROADMAP.md) — build plan
- `docs/` — guides (getting started, contributor guide, in progress)

## License

Apache 2.0. See [LICENSE](LICENSE).