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
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

from winnow.answer import Answer, Synthesizer, citation_answer
from winnow.config import ConfigError, PipelineConfig, load_config
from winnow.core.models import Chunk, SearchHit
from winnow.docstore import DocStore
from winnow.errors import PipelineError
from winnow.factories import build_embedder, build_indexer
from winnow.index.base import Indexer
from winnow.pipeline.engine import PipelineEngine, PipelineResult
from winnow.sources.base import SourceError

ConfigLike = str | Path | PipelineConfig


def _load(config: ConfigLike) -> PipelineConfig:
    if isinstance(config, PipelineConfig):
        return config
    return load_config(config)


async def run_async(
    config: ConfigLike,
    *,
    indexer: Indexer | None = None,
    incremental: bool = False,
    state_path: str | Path | None = None,
) -> PipelineResult:
    """Run a single pipeline asynchronously.

    With ``incremental=True``, a ``DocStore`` at ``state_path``
    (default ``.winnow/state.json``) tracks per-source content fingerprints;
    documents unchanged since the last successful run are skipped.
    """
    docstore = None
    if incremental:
        docstore = DocStore(Path(state_path) if state_path else DocStore.default_path())
    return await PipelineEngine(_load(config), indexer=indexer).run(docstore=docstore)


def run(config: ConfigLike, *, indexer: Indexer | None = None, incremental: bool = False,
        state_path: str | Path | None = None) -> PipelineResult:
    """Run a single pipeline synchronously (drop-in for celery/scripts).

    Wraps ``asyncio.run`` — safe to call from a thread with no running
    event loop.
    """
    return asyncio.run(
        run_async(config, indexer=indexer, incremental=incremental, state_path=state_path)
    )


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


def describe(config: ConfigLike) -> str:
    """Human-readable summary of a pipeline config (dry-run output).

    Works for a YAML path, a loaded :class:`PipelineConfig`, or a config
    built with the :mod:`winnow.dsl` builders. Informational only — does
    not validate that adapters are supported.
    """
    return PipelineEngine(_load(config)).describe()


async def query_async(
    config: ConfigLike,
    text: str,
    *,
    top_k: int = 5,
    indexer: Indexer | None = None,
) -> list[SearchHit]:
    """Retrieve the chunks closest to ``text`` from the configured index.

    Requires the config to define an ``index`` stage; the query string is
    embedded with the config's embedder, then searched like any chunk. When
    querying an in-memory index, pass the same ``indexer`` instance that
    produced the data (other backends re-build their client from config).
    """
    cfg = _load(config)
    if not text.strip():
        raise ConfigError("query text must not be empty")
    embedder = build_embedder(cfg.embed, enable_cache=False)
    if indexer is None:
        if cfg.index is None:
            raise ConfigError("query requires an 'index' stage in the config")
        indexer = build_indexer(cfg.index)
    vector = await embedder.embed(Chunk(text=text, source_uri="winnow:query"))
    return await indexer.search(vector, top_k=top_k)


def query(
    config: ConfigLike,
    text: str,
    *,
    top_k: int = 5,
    indexer: Indexer | None = None,
) -> list[SearchHit]:
    """Synchronous variant of :func:`query_async`."""
    return asyncio.run(query_async(config, text, top_k=top_k, indexer=indexer))


async def answer_async(
    config: ConfigLike,
    text: str,
    *,
    top_k: int = 5,
    indexer: Indexer | None = None,
    llm: Synthesizer | None = None,
) -> Answer:
    """Retrieve the closest chunks and synthesize a cited answer.

    With ``llm`` set, the hits are handed to a chat-completions endpoint that
    answers with ``[N]`` citations tied to ``SearchHit.source_uri``. Without
    an LLM (or if the call fails), falls back to numbering the excerpts
    itself, so answering is possible with the ``default`` embedder and no
    API key.
    """
    hits = await query_async(config, text, top_k=top_k, indexer=indexer)
    if llm is None:
        return citation_answer(text, hits)
    try:
        return await llm.answer(text, hits)
    except SourceError:
        return citation_answer(text, hits, reason="LLM call failed")


def answer(
    config: ConfigLike,
    text: str,
    *,
    top_k: int = 5,
    indexer: Indexer | None = None,
    llm: Synthesizer | None = None,
) -> Answer:
    """Synchronous variant of :func:`answer_async`."""
    return asyncio.run(answer_async(config, text, top_k=top_k, indexer=indexer, llm=llm))


def run_many(
    configs: Iterable[ConfigLike],
    *,
    max_parallel: int | None = None,
) -> list[PipelineResult]:
    """Synchronous variant of :func:`run_many_async`."""
    return asyncio.run(run_many_async(configs, max_parallel=max_parallel))


async def _tick_once(
    engines: Iterable[PipelineEngine],
    docstore_path: Path,
    semaphore: asyncio.Semaphore | None,
) -> list[PipelineResult | BaseException]:
    """Run every pipeline once; per-pipeline failures become exception elements."""

    async def _one(engine: PipelineEngine) -> PipelineResult | BaseException:
        if semaphore is None:
            return await _run(engine, docstore_path)
        async with semaphore:
            return await _run(engine, docstore_path)

    return list(await asyncio.gather(*(_one(e) for e in engines)))


async def _run(engine: PipelineEngine, docstore_path: Path) -> PipelineResult | BaseException:
    try:
        return await engine.run(docstore=DocStore(docstore_path))
    except (PipelineError, SourceError, ConfigError) as exc:
        return exc


async def watch_async(
    configs: Iterable[ConfigLike],
    *,
    interval: float = 60.0,
    max_parallel: int | None = None,
    state_path: str | Path | None = None,
    indexer: Indexer | None = None,
) -> AsyncIterator[list[PipelineResult | BaseException]]:
    """Re-run pipelines periodically until the caller stops iterating.

    Each iteration is an incremental run tracked by a ``DocStore`` at
    ``state_path`` (default ``.winnow/state.json``), so unchanged documents
    are skipped — and, thanks to cheap listing, not even downloaded. Steady
    state is a near-free tick; only what changed gets extracted, chunked, and
    embedded. The first tick runs immediately; subsequent ticks start
    ``interval`` seconds after the previous one finished.

    Failures are soft: a config that raises shows up as a ``BaseException``
    element in the yielded list (in config order) and the loop keeps going,
    so a transient source outage does not kill the scheduler. ``break`` or
    cancel the iteration to stop watching.

    Example — poll a source every 30 s until Ctrl+C::

        async for tick in watch_async(["pipeline.yaml"], interval=30):
            for result in tick:
                if isinstance(result, BaseException):
                    log.error("tick failed", exc_info=result)
                elif result.documents_changed:
                    log.info("%s changed documents", result.documents_changed)
    """
    pipelines = list(configs)
    if not pipelines:
        return
    if interval <= 0:
        raise ConfigError(f"watch interval must be > 0, got {interval}")
    engines = [PipelineEngine(_load(c), indexer=indexer) for c in pipelines]
    semaphore = asyncio.Semaphore(max_parallel) if max_parallel else None
    docstore_path = Path(state_path) if state_path else DocStore.default_path()

    while True:
        yield await _tick_once(engines, docstore_path, semaphore)
        await asyncio.sleep(interval)