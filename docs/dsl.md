# Describing pipelines in Python

YAML is Alvis's no-code interface; pipelines described in Python use the
same config objects (`alvis.config.models.PipelineConfig`), so they
validate, dry-run, and run identically to their YAML twins.

## Assembling a pipeline

```python
from alvis import dsl, describe, run

config = dsl.pipeline(
    dsl.s3(
        url="http://localhost:9000",
        bucket="alvis",
        access_key_env="MINIO_ACCESS_KEY",
        secret_key_env="MINIO_SECRET_KEY",
        exclude_globs=["**/*.mp4"],
    ),
    chunk=dsl.chunk(max_tokens=80),
    index=dsl.qdrant(url="http://localhost:6333", collection="alvis-s3"),
)

print(describe(config))          # dry-run summary
run(config)                      # sync run (own event loop)
```

## Stage builders

| builder                 | YAML equivalent | purpose                                   |
| ----------------------- | --------------- | ----------------------------------------- |
| `dsl.fs(path, pattern)` | `fs`            | text files under a directory              |
| `dsl.confluence(...)`   | `confluence`    | pages of a Confluence space               |
| `dsl.github(...)`       | `github`        | blobs of a repository tree                |
| `dsl.gitlab(project, url=)` | `gitlab`    | project blobs; self-hosted via `url`      |
| `dsl.s3(...)`           | `s3`            | S3 bucket objects (SigV4, no boto3)       |
| `dsl.extract()`         | `auto`          | markdown/html/plain/pdf/docx/xlsx → tree  |
| `dsl.chunk(max_tokens, overlap, strategy)` | `auto`  | auto/sections/size chunking |
| `dsl.embed_openai(...)` | `openai`        | OpenAI-compatible embedder API            |
| `dsl.qdrant(url, collection)` | `qdrant`  | Qdrant index                              |
| `dsl.pgvector(dsn, dsn_env, table)` | `pgvector` | PostgreSQL + pgvector index (`alvis[pgindex]`) |
| `dsl.memory()`          | `memory`        | in-memory index (tests)                   |

Source builders accept the same keys as the YAML `config` blocks; defaults
are omitted from the underlying config, and `pipeline(...)` overrides only
the stages you pass. Type-unsafe string keys are replaced by typed
parameters with docstrings.

## Running

- `run(config)` / `run_async(config)` — one pipeline, sync/async.
- `run_many([...], max_parallel=N)` / `run_many_async(...)` — several
  pipelines concurrently on one event loop.
- `query(config, text, top_k=N)` / `query_async(...)` — retrieve the closest
  chunks to a query string from the configured index (returns `SearchHit`s
  with cosine scores).
- `describe(config)` — human-readable dry-run summary.

## Embedding cache

`dsl.embed_openai(..., cache=...)` enables embedding reuse so unchanged
chunks are not re-embedded on later runs:

- `cache=True` — in-memory (per-process, dedups within a run).
- `cache={"path": ".alvis/embeddings.cache"}` — on-disk, survives restarts.
- omitted/`False` — no cache (default).

```python
cfg = dsl.pipeline(
    dsl.fs("docs"),
    embed=dsl.embed_openai(
        base_url="https://api.openai.com/v1",
        model="text-embedding-3-small",
        cache={"path": ".alvis/embeddings.cache"},
    ),
    index=dsl.qdrant(url="http://localhost:6333", collection="alvis_docs"),
)
```

Cache keys are content hashes scoped by model signature, so switching models
never serves stale vectors. Query embeddings are never cached.

See also [examples/python_dsl.py](../examples/python_dsl.py), which mirrors
`examples/s3-qdrant.yaml` and runs against the docker-compose stack.