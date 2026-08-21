# Operations: status, metrics, .env, scaffolding

Everything an operator touches day to day — answers "what is my knowledge base
ingesting, and is it working?" — without needing a browser or a database shell.
All commands are zero-dependency beside the package itself.

## `alvis status` — where does each pipeline stand?

Reads the run ledger, the source's live listing, the doc-store contents and
the index point count, and prints one report per pipeline:

```
$ alvis status
examples/hello-pipeline.yaml: fs source → memory
  source health: OK (1 documents)
  documents known: 0
  last run: never
  index points: 0
```

```
$ alvis status alvis/pipelines/confluence.yaml alvis/pipelines/s3.yaml --probe
alvis/pipelines/confluence.yaml: confluence source → qdrant (collection=alvis_docs)
  source health: OK (142 documents)
  documents known: 142
  last run: 2026-08-19T20:43:05+00:00 (ok, 0.22s)
    documents 142 | chunks 389 | changed 12 | skipped 130 | deleted 0
    embed cache: 318 hits / 71 misses
  index points: 389
alvis/pipelines/s3.yaml: s3 source → qdrant (collection=alvis-s3)
  source health: FAILED: PermissionDenied: ...   # the probe is best-effort
  documents known: 8
  last run: 2026-08-19T21:03:41+00:00 (FAILED: source 'fs' failed ..., 0.41s)
  index points: 24
```

Without an argument it uses the same defaults as `alvis run`
(`alvis/pipelines/*.yaml` plus `./alvis.yaml`).

- **source health** — cheap listing probe (`list_documents`); a failure here
  means the source is unreachable or reconfigured, even when the last run was
  fine. Sources without listing show `not probeable`.
- **documents known** — how many URIs the doc-store (`.alvis/state.json`)
  tracks from the last *successful* run for this pipeline.
- **last run** — from the run ledger: timestamp, ok/failed, duration, and the
  per-run document/chunk/change/skip/delete counts plus embed-cache hits/misses.
  Failures are recorded too, so a crash is visible here even before the logs.
- **index points** — the live content of the index. `memory` reports instantly;
  remote indexes are *not* queried unless you pass `--probe` (a round-trip that
  can take a few seconds), in which case an unreachable index shows
  `unreachable` rather than failing the command.

Options: `--state <path>` to point at a non-default ledger, `--env-file`,
`--plugins`, and `--json` for machine-readable output (a list of per-pipeline
dicts). Invalid configs are reported inline instead of aborting the whole run.

The ledger survives restarts: each pipeline keeps at most **10** most recent
run records, newest first, in the same state file as the doc-store
(`{"version": 2, "sources": ..., "runs": ...}`).

## `alvis metrics` and `alvis run --metrics-port`

Ingestion records into the process-wide metric store (see
[docs/observability.md](observability.md)). Expose it over HTTP:

```
alvis metrics --port 8000            # serve /metrics on 127.0.0.1:8000
```

For a long-lived scheduler, attach the endpoint to the run itself — same
process, so scrapes see live data from the moment ingestion starts:

```
alvis run alvis/pipelines/*.yaml --watch --metrics-port 8000 --metrics-host 0.0.0.0
```

The endpoint serves Prometheus text exposition. With `alvis[observability]`
installed it renders the real collector registry; otherwise it falls back to a
built-in renderer of the in-memory store (`render_plain_text`), which produces
the same metric names — either way Grafana works with the packaged dashboard
(see below). An idle process legitimately returns an empty body.

Ready-to-run monitoring with one command:

```
cd docker/observability
docker compose up -d --build
#   Qdrant     localhost:6333        Grafana    localhost:3000 (admin/admin)
#   Prometheus localhost:9090        panel      "Alvis — ingestion flows"
```

## Scaffolding configs: `alvis init`

`alvis init` writes a runnable `alvis.yaml`. Choose adapters:

```
alvis init                          # confluence source + qdrant index
alvis init --source fs --index sqlite
alvis init --source github --index pgvector     # pgvector uses dsn_env: POSTGRES_DSN
```

Available sources: `fs`, `confluence`, `github`, `gitlab`, `s3`, `static_url`;
available
indexes: `memory`, `sqlite`, `qdrant`, `pgvector`. It refuses to overwrite an existing
file unless `--force`.

## Secrets: `.env` files and fail-fast validation

Configs reference secrets by *name* (`api_token_env`, `access_key_env`,
`dsn_env`, ...) and adapters read them from the environment at request time.
Alvis will not silently start with a half-configured source:

- `alvis run`/`validate`/`query`/`status` load an explicit `--env-file`, or a
  conventional `./.env` in the project root when present. A tiny built-in
  parser handles `KEY=VALUE`, quoted values and comments — no `python-dotenv`
  dependency.
- `alvis run` **fails fast** (exit 1, before any network I/O) when a
  referenced variable is unset or empty:

```
$ alvis run alvis.yaml
Missing environment variable(s) referenced by the config: CONFLUENCE_API_TOKEN
Set them in the shell or point --env-file at a .env file.
```

- `alvis validate` reports the same as a validation problem (visible in
  `--json` output too).
- `alvis status` surfaces it per pipeline so a misconfigured workflow shows
  up in the same view as everything else.

```bash
# .env  (never commit this — add to .gitignore)
CONFLUENCE_API_TOKEN="..."
MINIO_ACCESS_KEY="..."
MINIO_SECRET_KEY="..."
```

## Related

- [docs/observability.md](observability.md) — metric families, logging events, tracing
- [docs/getting-started.md](getting-started.md) — install and first pipeline