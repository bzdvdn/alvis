"""Programmatic entry points — run one or many pipelines, sync or async.

For embedding Winnow into your own application (workers, schedulers,
FastAPI routes, ...) use the functions here:

- ``run`` / ``run_async``: a single pipeline, sync and async variants.
- ``run_many`` / ``run_many_async``: several pipelines concurrently on
  one event loop (the pipeline is I/O-bound, so concurrency is cheap).

Celery-style sync workers simply call ``run("pipeline.yaml")`` inside a
task; async frameworks can await ``run_async(...)`` directly. Multi-pipeline
runs share a single event loop: each pipeline gets its own source/index
adapters, so there is no shared mutable state between them. When pipelines
target the same collection, give them distinct source identities or separate
collections to avoid reconcile races.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path

from winnow.config import PipelineConfig, load_config
from winnow.index.base import Indexer
from winnow.pipeline.engine import PipelineEngine, PipelineResult

ConfigLike = str | Path | PipelineConfig


def _load(config: ConfigLike) -> PipelineConfig:
    if isinstance(config, PipelineConfig):
        return config
    return load_config(config)


async def run_async(
    config: ConfigLike,
    *,
    indexer: Indexer | None = None,
) -> PipelineResult:
    """Run a single pipeline asynchronously."""
    return await PipelineEngine(_load(config), indexer=indexer).run()


def run(config: ConfigLike, *, indexer: Indexer | None = None) -> PipelineResult:
    """Run a single pipeline synchronously (drop-in for celery/scripts).

    Wraps ``asyncio.run`` — safe to call from a thread with no running
    event loop.
    """
    return asyncio.run(run_async(config, indexer=indexer))


async def run_many_async(
    configs: Iterable[ConfigLike],
    *,
    max_parallel: int | None = None,
) -> list[PipelineResult]:
    """Run multiple pipelines concurrently on a single event loop.

    ``max_parallel`` caps concurrency (None = unlimited). If one pipeline
    raises, the exception propagates and the rest are cancelled; use
    :func:`run_many` for fail-soft behavior, or wrap individual configs.
    """
    engines = [PipelineEngine(_load(c)) for c in configs]
    semaphore = asyncio.Semaphore(max_parallel) if max_parallel else None

    async def _one(engine: PipelineEngine) -> PipelineResult:
        if semaphore is None:
            return await engine.run()
        async with semaphore:
            return await engine.run()

    return list(await asyncio.gather(*(_one(e) for e in engines)))


def run_many(
    configs: Iterable[ConfigLike],
    *,
    max_parallel: int | None = None,
) -> list[PipelineResult]:
    """Synchronous variant of :func:`run_many_async`."""
    return asyncio.run(run_many_async(configs, max_parallel=max_parallel))