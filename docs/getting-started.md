# Getting started

## Install

```bash
python -m venv .venv
.venv/bin/pip install -e .          # core
.venv/bin/pip install -e .[documents]   # + PDF/DOCX/XLSX parsing
.venv/bin/pip install -e .[pgindex]     # + PostgreSQL/pgvector index
```

Needs Python ≥ 3.10.

## Your first pipeline

Scaffold a config, validate it, then run it:

```bash
winnow init                # writes winnow.yaml
winnow validate winnow.yaml
winnow run examples/hello-pipeline.yaml   # zero-dependency hello world
```

The YAML grammar is one pipeline with one source and up to five stages:

```yaml
pipeline:
  source:
    type: fs
    config:
      path: ./docs
  index:
    type: memory
```

`fs → memory` needs nothing else. For retrieval after ingestion:

```bash
winnow run winnow.yaml
winnow query winnow.yaml --text "your question" --top-k 5
```

## A real-source example: GitLab → Qdrant

```yaml
pipeline:
  source:
    type: gitlab
    config:
      url: https://gitlab.example.com
      project: team/kb
      branch: main
      include_globs: ["**/*.md"]
      api_token_env: GITLAB_TOKEN
  index:
    type: qdrant
    config:
      url: http://localhost:6333
      collection: team_kb
```

Run against local services (real Qdrant, mock Confluence, MinIO) with
docker-compose — see the repo README for the full walkthrough.

## Next steps

- Browse the [DSL guide](dsl.md) to describe pipelines from Python.
- Read [CONSTITUTION.md](../CONSTITUTION.md) for the project's purpose.
- Write your own source — [how to write a connector](../CONTRIBUTING.md).