"""Observability hooks — structured logging, metrics, optional Prometheus/OTel.

Everything here is safe to use with *zero* extra dependencies:

- **Logging**: a ``winnow`` logger with a JSON or text-formatted handler,
  configured once via :func:`setup_logging`. Pipeline events carry a
  ``extra`` dict of key/value fields (source, index, ticket counts, stage
  durations).
- **Metrics**: a process-global :class:`Metrics` store implemented on the
  stdlib — counters and cumulative histograms keyed by ``(name, labels)``.
  Install ``winnow[observability]`` and construct ``Metrics(use_prometheus=True)``
  to back the same calls with real Prometheus collectors (readable via
  :meth:`Metrics.export_prometheus`).
- **Traces**: :func:`span` is a no-op ``AsyncExitStack`` unless a tracer is
  attached with :func:`configure_observability` — pass an OpenTelemetry
  ``Tracer`` to get per-run spans ``pipeline.run``.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import math
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any

LOG = logging.getLogger("winnow")

# Prometheus requires lowercase/snake_case names without dots.
_METRIC_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    float("inf"),
)

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

_HANDLER: logging.Handler | None = None

_NOISE_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
    }
)


def _event_fields(record: logging.LogRecord) -> dict[str, Any]:
    """Structured fields carried by a record.

    ``Logger.info(extra={...})`` merges the keys straight into the record's
    attributes (e.g. ``record.source``), so anything that is not part of the
    standard ``LogRecord`` surface is an event field. An explicit ``extra``
    attribute (used by tests and library callers) is preferred when present.
    """
    explicit = record.__dict__.get("extra")
    if isinstance(explicit, dict):
        return explicit
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _NOISE_FIELDS and not key.startswith("_")
    }


class _JsonFormatter(logging.Formatter):
    """Renders one JSON object per line: ts/level/event plus structured fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(_event_fields(record))
        return json.dumps(payload, default=str)


class _TextFormatter(logging.Formatter):
    """Human-readable rows with ``key=value`` fields appended."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        fields = _event_fields(record)
        if fields:
            line += " " + " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
        return line


def setup_logging(*, level: int | str = logging.INFO, json: bool = False) -> None:
    """Attach a handler to the ``winnow`` logger (stderr); idempotent.

    Calling again replaces the previous handler, so toggling ``json`` or the
    level at runtime works. Structured fields passed via ``extra={...}`` are
    rendered as a JSON object (``json=True``) or ``key=value`` suffixes
    (text formatter).
    """
    global _HANDLER
    winnow = logging.getLogger("winnow")
    if _HANDLER is not None:
        winnow.removeHandler(_HANDLER)
    handler = logging.StreamHandler()
    if json:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(_TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    winnow.addHandler(handler)
    winnow.setLevel(level)
    winnow.propagate = False
    _HANDLER = handler


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


@dataclass
class _Counter:
    value: int = 0

    def inc(self, amount: int) -> None:
        self.value += amount


@dataclass
class _Histogram:
    buckets: tuple[float, ...] = _METRIC_BUCKETS
    counts: defaultdict[float, int] = field(default_factory=lambda: defaultdict(int))
    sum: float = 0.0
    count: int = 0

    def observe(self, value: float) -> None:
        self.count += 1
        self.sum += value
        for bucket in self.buckets:
            if value <= bucket:
                self.counts[bucket] += 1


def _label_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((labels or {}).items()))


@dataclass
class Metrics:
    """In-process metrics store; optionally backed by real Prometheus collectors.

    Default (``use_prometheus=False``) keeps counters/histograms in memory with
    the stdlib only — inspect them with :meth:`snapshot` or export them with
    :meth:`export_prometheus` once the extra is installed. With
    ``use_prometheus=True`` every :meth:`inc`/:meth:`observe` updates a real
    ``prometheus_client`` Counter/Histogram registered in a private registry.
    """

    use_prometheus: bool = False
    _counters: dict[str, dict[tuple[tuple[str, str], ...], _Counter]] = field(
        default_factory=dict
    )
    _histograms: dict[str, dict[tuple[tuple[str, str], ...], _Histogram]] = field(
        default_factory=dict
    )
    _prom: Any | None = field(default=None, init=False, repr=False)
    _prom_registry: Any | None = field(default=None, init=False, repr=False)
    _prom_counter_families: dict[str, dict[tuple[str, ...], Any]] = field(
        default_factory=dict
    )
    _prom_histogram_families: dict[str, dict[tuple[str, ...], Any]] = field(
        default_factory=dict
    )

    @staticmethod
    def _prometheus_available() -> bool:
        return importlib.util.find_spec("prometheus_client") is not None

    def _ensure_prometheus(self) -> Any:
        if self._prom is None:
            if not self._prometheus_available():
                raise RuntimeError(
                    "Prometheus backend requested, but prometheus_client is not "
                    "installed. Add winnow[observability] to your dependencies."
                )
            pc = importlib.import_module("prometheus_client")
            self._prom = pc
            self._prom_registry = pc.CollectorRegistry()
        return self._prom

    def inc(self, name: str, *, amount: int = 1, labels: dict[str, str] | None = None) -> None:
        """Increment a counter (or create it)."""
        key = _label_key(labels)
        if self.use_prometheus:
            labelnames = dict(key)
            collector = self._prom_counter(name, key)
            collector.labels(*(labelnames[k] for k in labelnames)).inc(amount)
            return
        self._counters.setdefault(name, {}).setdefault(key, _Counter()).inc(amount)

    def observe(
        self, name: str, *, value: float, labels: dict[str, str] | None = None
    ) -> None:
        """Record an observation into a histogram (or create it)."""
        key = _label_key(labels)
        if self.use_prometheus:
            labelnames = dict(key)
            collector = self._prom_histogram(name, key)
            collector.labels(*(labelnames[k] for k in labelnames)).observe(float(value))
            return
        self._histograms.setdefault(name, {}).setdefault(key, _Histogram()).observe(
            float(value)
        )

    @contextmanager
    def timer(
        self, name: str, *, labels: dict[str, str] | None = None
    ) -> Iterator[None]:
        """Time a block and record the elapsed seconds into ``name``."""
        started = time.monotonic()
        try:
            yield
        finally:
            self.observe(name, value=time.monotonic() - started, labels=labels)

    def _prom_counter(self, name: str, key: tuple[tuple[str, str], ...]) -> Any:
        pc = self._ensure_prometheus()
        labelnames = tuple(dict(key))
        family = self._prom_counter_families.setdefault(name, {})
        collector = family.get(labelnames)
        if collector is None:
            collector = pc.Counter(
                name,
                name.replace("_", " "),
                list(labelnames),
                registry=self._prom_registry,
            )
            family[labelnames] = collector
        return collector

    def _prom_histogram(self, name: str, key: tuple[tuple[str, str], ...]) -> Any:
        pc = self._ensure_prometheus()
        labelnames = tuple(dict(key))
        family = self._prom_histogram_families.setdefault(name, {})
        collector = family.get(labelnames)
        if collector is None:
            collector = pc.Histogram(
                name,
                name.replace("_", " "),
                list(labelnames),
                buckets=list(_METRIC_BUCKETS),
                registry=self._prom_registry,
            )
            family[labelnames] = collector
        return collector

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return the stdlib store as nested dicts (counters + histograms).

        ``histograms`` render as ``{"count": int, "sum": float,
        "buckets": {"<upper bound>": count}}`` — cumulative, so ``buckets["1.0"]``
        counts every observation ≤ 1.0 s.
        """
        counters = {
            name: {_format_key(k): c.value for k, c in family.items()}
            for name, family in sorted(self._counters.items())
        }
        histograms = {
            name: {
                _format_key(k): {
                    "count": h.count,
                    "sum": h.sum,
                    "buckets": {str(bucket): n for bucket, n in sorted(h.counts.items())},
                }
                for k, h in family.items()
            }
            for name, family in sorted(self._histograms.items())
        }
        return {"counters": counters, "histograms": histograms}

    def export_prometheus(self) -> bytes:
        """Render the collected families as Prometheus text exposition format."""
        pc = self._ensure_prometheus()
        text: bytes = pc.generate_latest(registry=self._prom_registry)
        return text

    def render_plain_text(self) -> bytes:
        """Render the in-memory families without a Prometheus backend.

        Produces valid Prometheus text exposition: counters as ``counter``
        families and histograms as ``histogram`` families (``_bucket`` lines at
        the configured thresholds plus ``_sum`` and ``_count``). The metrics
        endpoint uses this when ``prometheus_client`` is missing or the stdlib
        store is in use.
        """
        lines: list[str] = []
        for name, family in sorted(self._counters.items()):
            lines.append(f"# HELP {name} {name.replace('_', ' ')}")
            lines.append(f"# TYPE {name} counter")
            for key, counter in family.items():
                lines.append(f"{name}{_format_labels(key)} {counter.value}")
        for name, hist_family in sorted(self._histograms.items()):
            lines.append(f"# HELP {name} {name.replace('_', ' ')}")
            lines.append(f"# TYPE {name} histogram")
            for key, hist in hist_family.items():
                for bucket in hist.buckets:
                    if bucket == math.inf:
                        continue
                    le = _with_le(key, _format_number(bucket))
                    lines.append(f"{name}_bucket{{{le}}} {hist.counts[bucket]}")
                lines.append(
                    f"{name}_bucket{{{_with_le(key, '+Inf')}}} {hist.count}"
                )
                lines.append(f"{name}_sum{_format_labels(key)} {_format_number(hist.sum)}")
                lines.append(f"{name}_count{_format_labels(key)} {hist.count}")
        return ("\n".join(lines) + "\n").encode() if lines else b""

    def reset(self) -> None:
        """Drop every collected sample (useful between test runs/watch resets)."""
        self._counters.clear()
        self._histograms.clear()
        self._prom_counter_families.clear()
        self._prom_histogram_families.clear()
        self._prom = None
        self._prom_registry = None


def _format_key(key: tuple[tuple[str, str], ...]) -> str:
    return " ".join(f"{k}={v}" for k, v in key)


def _format_labels(key: tuple[tuple[str, str], ...]) -> str:
    """Render a label set for the Prometheus text format (``{k="v",...}``)."""
    if not key:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in key) + "}"


def _with_le(key: tuple[tuple[str, str], ...], le: str) -> str:
    """``_format_labels`` plus a ``le`` upper-bound label (histogram buckets)."""
    parts = [f'le="{le}"']
    parts.extend(f'{k}="{v}"' for k, v in key)
    return ",".join(parts)


def _format_number(value: float) -> str:
    """Compact, locale-free representation of a sample value."""
    if value == math.inf:
        return "+Inf"
    if value == -math.inf:
        return "-Inf"
    return f"{value:g}"


# --------------------------------------------------------------------------
# Process-wide hooks (what the pipeline engine consults)
# --------------------------------------------------------------------------

_metrics = Metrics()
_tracer: Any | None = None


def get_metrics() -> Metrics:
    """The process-wide metrics store the engine records against."""
    return _metrics


def configure_observability(
    *, metrics: Metrics | None = None, tracer: Any | None = None
) -> None:
    """Replace the process-wide metrics/tracer the engine uses."""
    global _metrics, _tracer
    if metrics is not None:
        _metrics = metrics
    _tracer = tracer


def reset_observability() -> None:
    """Restore pristine defaults (a fresh stdlib ``Metrics``, no tracer)."""
    global _metrics, _tracer
    _metrics = Metrics()
    _tracer = None


def inc(name: str, *, amount: int = 1, labels: dict[str, str] | None = None) -> None:
    _metrics.inc(name, amount=amount, labels=labels)


def observe(name: str, *, value: float, labels: dict[str, str] | None = None) -> None:
    _metrics.observe(name, value=value, labels=labels)


def timer(
    name: str, *, labels: dict[str, str] | None = None
) -> AbstractContextManager[None]:
    return _metrics.timer(name, labels=labels)


@asynccontextmanager
async def span(
    name: str, *, attributes: dict[str, Any] | None = None
) -> AsyncIterator[None]:
    """Open a traced span around a block; a no-op without a configured tracer.

    Pass an OpenTelemetry ``Tracer`` to :func:`configure_observability` to
    emit real ``pipeline.*`` spans; otherwise this yields immediately.
    """
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name, attributes=attributes or {}):
        yield