# Observability

Winnow ships production-observability hooks with **zero required
dependencies**. Structured logging, metrics and tracing are available out of
the box; the real Prometheus / OpenTelemetry backends activate when you
install the optional extra and opt in.

- `winnow[observability]` → `prometheus-client`, `opentelemetry-api`,
  `opentelemetry-sdk`.

## Logging

Every pipeline run emits structured events on the `winnow` logger. With the
JSON formatter each event is one JSON line (`ts`, `level`, `event`, plus the
key/value fields), so it drops straight into Loki/ELK/CloudWatch:

```
{"ts":"2026-08-19T10:11:12+0000","level":"INFO","logger":"winnow","event":"pipeline.completed","source":"fs","index":"memory","duration_s":0.1234,"documents":2,"chunks":5,"changed":2,"skipped":0,"deleted":0,"embed_cache_hits":0,"embed_cache_misses":0}
```

Configure once:

```python
from winnow.observability import setup_logging

setup_logging(level="INFO", json=True)   # go JSON; setup_logging(json=False) for text
```

The text formatter appends the same fields as `key=value` suffixes. Events
emitted by the engine:

- `pipeline.completed` — per-run totals + duration (INFO).
- `pipeline.failed` — source/stage failure with source label (ERROR).
- `artifact.processed` — per-document chunk/byte counts (DEBUG).
- `embed.batch` — batch size for embedding (DEBUG).
- `watch.tick` / `watch.tick_failed` — per-pipeline result of a `--watch`
  tick (INFO/ERROR).

CLI: `winnow run --log-json --log-level info pipeline.yaml`.

## Metrics

The engine records against the process-wide `winnow.observability` metrics
store. With the stdlib backend (the default) samples live in memory and are
read/inspected via `snapshot()`:

```python
from winnow.observability import get_metrics

snapshot = get_metrics().snapshot()
print(snapshot["counters"]["pipeline_runs_total"])       # {"source=fs index=memory": 3, ...}
print(snapshot["histograms"]["pipeline_duration_seconds"])
```

### Prometheus

`Metrics(use_prometheus=True)` backs every counter/histogram with a real
`prometheus_client` collector in a private registry:

```python
from winnow.observability import Metrics, configure_observability

metrics = Metrics(use_prometheus=True)
configure_observability(metrics=metrics)
```

The CLI exposes any metric store over HTTP without writing a line of Python.
Scrape it from Prometheus, or hit it by hand (see
[docs/status.md](status.md) for the operator view):

```
winnow metrics --port 8000           # GET /metrics on 127.0.0.1:8000
winnow run --watch --metrics-port 8000 --metrics-host 0.0.0.0 pipeline.yaml
```

`--metrics-port` serves `/metrics` from the *same process* that ingests, so a
scrape reflects live runs. When `prometheus_client` is installed the registry
backing is used; otherwise the endpoint renders the same families from the
in-memory store via `Metrics.render_plain_text()` — both produce valid,
identically-named exposition, so Grafana works either way.

### Metric families

All names use Prometheus-legal snake_case and carry `{source, index}` labels
(`index="none"` when no index stage is configured). Stage timers add a
`stage` label.

| Family | Type | Meaning |
| --- | --- | --- |
| `pipeline_runs_total` | counter | completed runs |
| `pipeline_failures_total` | counter | failed runs |
| `pipeline_documents_total` | counter | documents seen |
| `pipeline_chunks_total` | counter | chunks produced |
| `pipeline_changed_total` | counter | changed (reprocessed) docs |
| `pipeline_skipped_total` | counter | unchanged (skipped) docs |
| `pipeline_deleted_total` | counter | pruned/reconciled docs |
| `pipeline_duration_seconds` | histogram | full-run latency |
| `pipeline_stage_seconds` | histogram | latency per stage (`list`, `fetch`, `extract`, `embed`, `upsert`, `reconcile`, `commit`) |

## Tracing

`configure_observability(tracer=...)` accepts any object exposing
`start_as_current_span` (an OpenTelemetry `Tracer`). The engine then opens a
`pipeline.run` span per pipeline with `source`/`index` attributes; without a
tracer, `span()` is a no-op and there is no overhead:

```python
from opentelemetry import trace

configure_observability(tracer=trace.get_tracer("winnow"))
```

## Long-running processes

For `--watch` schedulers the recommended setup is:

```
winnow run --watch --log-json --log-level info --interval 30 \
    --metrics-port 8000 --metrics-host 0.0.0.0 pipeline.yaml
```

…with Prometheus scraping `:8000/metrics` and alerting on
`pipeline_failures_total` and `pipeline_duration_seconds` SLOs.

A zero-touch stack (Qdrant + Winnow + Prometheus + Grafana, dashboard
included) ships in `docker/observability/`:

```
cd docker/observability
docker compose up -d --build
# Grafana at http://localhost:3000 (admin/admin) → dashboard "Winnow"
```