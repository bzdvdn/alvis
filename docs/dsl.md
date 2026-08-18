# Describing pipelines in Python

YAML is Winnow's no-code interface; pipelines described in Python use the
same config objects (`winnow.config.models.PipelineConfig`), so they
validate, dry-run, and run identically to their YAML twins.

## Assembling a pipeline

```python
from winnow import dsl, describe, run

config = dsl.pipeline(
    dsl.s3(
        url="http://localhost:9000",
        bucket="winnow",
        access_key_env="MINIO_ACCESS_KEY",
        secret_key_env="MINIO_SECRET_KEY",
        exclude_globs=["**/*.mp4"],
    ),
    chunk=dsl.chunk(max_tokens=80),
    index=dsl.qdrant(url="http://localhost:6333", collection="winnow-s3"),
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
| `dsl.chunk(max_tokens, overlap, strategy)` | `auto`  | auto chunking (`sections` → chunk per heading) |
| `dsl.embed_openai(...)` | `openai`        | OpenAI-compatible embedder API            |
| `dsl.qdrant(url, collection)` | `qdrant`  | Qdrant index                              |
| `dsl.pgvector(dsn, dsn_env, table)` | `pgvector` | PostgreSQL + pgvector index (`winnow[pgindex]`) |
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

See also [examples/python_dsl.py](../examples/python_dsl.py), which mirrors
`examples/s3-qdrant.yaml` and runs against the docker-compose stack.