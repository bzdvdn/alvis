"""Observability — structured logging, metrics store, spans, Prometheus export."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from winnow import dsl, run_async, watch_async
from winnow.errors import PipelineError
from winnow.observability import (
    Metrics,
    _JsonFormatter,
    _TextFormatter,
    configure_observability,
    get_metrics,
    reset_observability,
    setup_logging,
)


class _Capture(logging.Handler):
    def __init__(self, records: list[logging.LogRecord]) -> None:
        super().__init__()
        self.records = records

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def captured_logs() -> Iterator[list[logging.LogRecord]]:
    records: list[logging.LogRecord] = []
    handler = _Capture(records)
    logger = logging.getLogger("winnow")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def _record(
    name: str, level: int = logging.INFO, extra: dict[str, object] | None = None
) -> logging.LogRecord:
    record = logging.LogRecord("winnow", level, __file__, 1, name, (), None)
    if extra is not None:
        record.extra = extra
    return record


def test_metrics_stdlib_backend() -> None:
    m = Metrics()
    m.inc("pipeline_runs_total", labels={"source": "fs", "index": "memory"})
    m.inc("pipeline_runs_total", labels={"source": "fs", "index": "memory"})
    m.inc("pipeline_failures_total", labels={"source": "s3"})
    m.observe("pipeline_duration_seconds", value=0.5, labels={"source": "fs"})
    m.observe("pipeline_duration_seconds", value=0.25, labels={"source": "fs"})

    snap = m.snapshot()
    assert snap["counters"]["pipeline_runs_total"]["index=memory source=fs"] == 2
    assert snap["counters"]["pipeline_failures_total"]["source=s3"] == 1
    hist = snap["histograms"]["pipeline_duration_seconds"]["source=fs"]
    assert hist["count"] == 2
    assert hist["sum"] == pytest.approx(0.75)
    assert hist["buckets"]["1.0"] == 2


def test_metrics_timer_records_bucket() -> None:
    m = Metrics()
    with m.timer("pipeline_stage_seconds", labels={"stage": "checkout"}):
        pass
    hist = m.snapshot()["histograms"]["pipeline_stage_seconds"]["stage=checkout"]
    assert hist["count"] == 1
    assert hist["sum"] >= 0


def test_render_plain_text_renders_counters_and_histograms() -> None:
    m = Metrics()
    m.inc("pipeline_runs_total", labels={"source": "fs", "index": "memory"})
    m.observe("pipeline_duration_seconds", value=0.07, labels={"source": "fs"})

    text = m.render_plain_text().decode()
    assert '# TYPE pipeline_runs_total counter' in text
    assert 'pipeline_runs_total{index="memory",source="fs"} 1' in text
    assert "# TYPE pipeline_duration_seconds histogram" in text
    assert 'pipeline_duration_seconds_bucket{le="0.1",source="fs"} 1' in text
    assert 'pipeline_duration_seconds_bucket{le="+Inf",source="fs"} 1' in text
    assert 'pipeline_duration_seconds_sum{source="fs"} 0.07' in text
    assert 'pipeline_duration_seconds_count{source="fs"} 1' in text
    assert text.count("le=\"+Inf\"") == 1


def test_render_plain_text_empty_store() -> None:
    assert Metrics().render_plain_text() == b""


def test_json_formatter_renders_extra() -> None:
    formatter = _JsonFormatter()
    payload = json.loads(
        formatter.format(
            _record("pipeline.completed", extra={"source": "fs", "chunks": 3})
        )
    )
    assert payload["event"] == "pipeline.completed"
    assert payload["source"] == "fs"
    assert payload["chunks"] == 3
    assert {"ts", "level", "logger"} <= payload.keys()


def test_text_formatter_appends_fields() -> None:
    formatter = _TextFormatter("%(message)s")
    line = formatter.format(
        _record("pipeline.completed", extra={"source": "fs", "chunks": 3})
    )
    assert "pipeline.completed" in line
    assert "source=fs" in line
    assert "chunks=3" in line


def test_real_logger_path_renders_fields(
    captured_logs: list[logging.LogRecord],
) -> None:
    logging.getLogger("winnow").info("pipeline.completed", extra={"source": "fs", "chunks": 3})
    payload = json.loads(_JsonFormatter().format(captured_logs[-1]))
    assert payload["event"] == "pipeline.completed"
    assert payload["source"] == "fs"
    assert payload["chunks"] == 3
    assert "msg" not in payload


async def test_engine_records_metrics_logs_and_stage_timers(
    tmp_path: Path, captured_logs: list[logging.LogRecord]
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\nhello content here", encoding="utf-8")
    m = Metrics()
    configure_observability(metrics=m)
    try:
        result = await run_async(
            dsl.pipeline(dsl.fs(path=str(corpus)), index=dsl.memory())
        )
    finally:
        reset_observability()

    snap = m.snapshot()
    assert snap["counters"]["pipeline_runs_total"]["index=memory source=fs"] == 1
    assert (
        snap["counters"]["pipeline_documents_total"]["index=memory source=fs"]
        == result.documents_ingested
    )
    assert (
        snap["counters"]["pipeline_chunks_total"]["index=memory source=fs"]
        == result.chunks_indexed
    )
    duration = snap["histograms"]["pipeline_duration_seconds"]["index=memory source=fs"]
    assert duration["count"] == 1
    assert duration["sum"] >= 0

    stages = {
        key.split("stage=")[1]
        for key in snap["histograms"]["pipeline_stage_seconds"]
    }
    assert {"fetch", "extract", "embed", "upsert", "reconcile"} <= stages

    events = {r.getMessage() for r in captured_logs}
    assert "pipeline.completed" in events
    assert "artifact.processed" in events


async def test_failure_increments_failure_counter(tmp_path: Path) -> None:
    m = Metrics()
    configure_observability(metrics=m)
    try:
        with pytest.raises(PipelineError):
            await run_async(dsl.pipeline(dsl.fs(path=str(tmp_path / "missing"))))
    finally:
        reset_observability()

    snap = m.snapshot()
    assert snap["counters"]["pipeline_failures_total"]["index=none source=fs"] == 1
    assert "pipeline_runs_total" not in snap["counters"]


class _FakeTracer:
    def __init__(self) -> None:
        self.spans: list[tuple[str, dict[str, str]]] = []

    @contextmanager
    def start_as_current_span(self, name: str, attributes: dict[str, str] | None = None):
        self.spans.append((name, attributes or {}))
        yield


async def test_span_recorded_when_tracer_configured(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\nhello", encoding="utf-8")
    tracer = _FakeTracer()
    configure_observability(tracer=tracer)
    try:
        await run_async(dsl.pipeline(dsl.fs(path=str(corpus)), index=dsl.memory()))
    finally:
        reset_observability()
    assert ("pipeline.run", {"source": "fs", "index": "memory"}) in tracer.spans


def test_watch_emits_tick_logs(tmp_path: Path, captured_logs: list[logging.LogRecord]) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\nhello", encoding="utf-8")
    cfg = dsl.pipeline(dsl.fs(path=str(corpus)), index=dsl.memory())

    async def _drive() -> None:
        gen = watch_async([cfg], state_path=str(tmp_path / "state.json"), interval=0.001)
        await anext(gen)
        await gen.aclose()

    asyncio.run(_drive())
    events = {r.getMessage() for r in captured_logs}
    assert "watch.tick" in events
    assert "watch.tick_failed" not in events


def test_prometheus_backend_exports_text() -> None:
    pytest.importorskip("prometheus_client")
    m = Metrics(use_prometheus=True)
    m.inc("pipeline_runs_total", amount=3, labels={"source": "fs", "index": "memory"})
    m.observe("pipeline_duration_seconds", value=1.0, labels={"source": "fs"})

    text = m.export_prometheus().decode()
    assert "pipeline_runs_total{index=\"memory\",source=\"fs\"} 3.0" in text
    assert 'pipeline_duration_seconds_count{source="fs"} 1.0' in text


def test_prometheus_backend_requires_client() -> None:
    m = Metrics(use_prometheus=True)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(m, "_prometheus_available", staticmethod(lambda: False))
    try:
        with pytest.raises(RuntimeError, match="observability"):
            m.inc("pipeline_runs_total")
    finally:
        monkeypatch.undo()


def test_get_metrics_default_and_reset() -> None:
    reset_observability()
    assert get_metrics().snapshot()["counters"] == {}
    setup_logging(level=logging.INFO, json=False)
    reset_observability()
    assert get_metrics().snapshot()["counters"] == {}