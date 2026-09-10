"""``alvis run`` — execute (or dry-run, or watch) one or more pipelines."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import typer

from alvis.cli._metrics_server import _start_metrics_server
from alvis.cli._shared import (
    _enable_plugins,
    _fail_missing_env,
    _load_env_file,
    _resolve_configs,
    app,
)
from alvis.config import ConfigError
from alvis.docstore import DocStore
from alvis.errors import PipelineError
from alvis.observability import setup_logging
from alvis.pipeline.engine import PipelineEngine, PipelineResult
from alvis.registry import check_pipeline_supported


@app.command()
def run(
    configs: list[Path] = typer.Argument(  # noqa: B008
        None,
        help="Paths to pipeline YAML configs. Default: alvis/pipelines/*.yaml "
        "plus ./alvis.yaml. Multiple paths run in parallel.",
    ),
    plugin_dirs: list[Path] = typer.Option(  # noqa: B008
        None,
        "--plugins",
        help="Directories of local companion-plugin .py files to load.",
    ),
    env_file: Path | None = typer.Option(  # noqa: B008
        None,
        "--env-file",
        help="Load secrets from this file (default: ./.env if present).",
    ),
    dry_run: bool = typer.Option(  # noqa: B008
        False,
        "--dry-run",
        help="Validate and describe the pipelines without executing them.",
    ),
    parallel: int = typer.Option(  # noqa: B008
        4,
        "--parallel",
        help="Maximum number of pipelines to run concurrently.",
    ),
    incremental: bool = typer.Option(  # noqa: B008
        False,
        "--incremental",
        "-i",
        help="Skip documents unchanged since the last successful run.",
    ),
    state: Path | None = typer.Option(  # noqa: B008
        None,
        "--state",
        help="Incremental state file (default .alvis/state.json). "
        "Give distinct paths when running multiple configs in parallel.",
    ),
    watch: bool = typer.Option(  # noqa: B008
        False,
        "--watch",
        "-w",
        help="Keep running: poll the sources every --interval and ingest only "
        "what changed (implies --incremental). Stop with Ctrl+C.",
    ),
    interval: float = typer.Option(  # noqa: B008
        60.0,
        "--interval",
        help="Seconds between watch ticks.",
    ),
    log_json: bool = typer.Option(  # noqa: B008
        False,
        "--log-json",
        help="Emit structured JSON logs to stderr (default: human text).",
    ),
    log_level: str = typer.Option(  # noqa: B008
        "warning",
        "--log-level",
        help="Log verbosity: debug, info, warning or error.",
    ),
    metrics_host: str = typer.Option(  # noqa: B008
        "127.0.0.1",
        "--metrics-host",
        help="Interface for the Prometheus /metrics endpoint. Use 0.0.0.0 "
        "inside containers.",
    ),
    metrics_port: int | None = typer.Option(  # noqa: B008
        None,
        "--metrics-port",
        help="Serve live Prometheus metrics on this port while running.",
    ),
) -> None:
    """Run the configured ingestion pipeline(s)."""
    level = getattr(logging, log_level.upper(), None)
    if not isinstance(level, int):
        typer.echo(f"Error: unknown --log-level '{log_level}'", err=True)
        raise typer.Exit(2)
    setup_logging(level=level, json=log_json)
    if parallel < 1:
        typer.echo("Error: --parallel must be >= 1", err=True)
        raise typer.Exit(2)
    if interval <= 0:
        typer.echo("Error: --interval must be > 0", err=True)
        raise typer.Exit(2)
    if metrics_port is not None and metrics_port < 0:
        typer.echo("Error: --metrics-port must be >= 0", err=True)
        raise typer.Exit(2)
    _enable_plugins(plugin_dirs)
    _load_env_file(env_file)
    if watch and not incremental:
        incremental = True
        typer.echo("--watch implies --incremental; enabling incremental mode.")

    metrics_server = _start_metrics_server(metrics_host, metrics_port)

    configs = _resolve_configs(configs)
    engines: list[PipelineEngine] = []
    for config in configs:
        try:
            engine = PipelineEngine.from_yaml(config)
        except ConfigError as exc:
            typer.echo(f"Invalid config {config}:\n{exc}", err=True)
            raise typer.Exit(1) from exc
        _fail_missing_env(engine.config)
        engines.append(engine)

    if dry_run:
        invalid = False
        for config, engine in zip(configs, engines, strict=True):
            problems = check_pipeline_supported(
                source=engine.config.source.type,
                extract=engine.config.extract.strategy,
                chunk=engine.config.chunk.strategy,
                embed=engine.config.embed.type,
                index=engine.config.index.type if engine.config.index else None,
            )
            if problems:
                invalid = True
                typer.echo(
                    f"Invalid config {config}:\n"
                    + "\n".join(f"  {p}" for p in problems),
                    err=True,
                )
                continue
            typer.echo(f"Pipeline dry-run for {config}:")
            typer.echo(engine.describe())
            typer.echo("\n  Pipeline graph")
            typer.echo("\n".join(f"    {line}" for line in engine.describe_graph().splitlines()))
        if invalid:
            raise typer.Exit(1)
        return

    async def _run_all() -> list[PipelineResult | BaseException]:
        semaphore = asyncio.Semaphore(parallel)

        async def _one(engine: PipelineEngine) -> PipelineResult | BaseException:
            async with semaphore:
                try:
                    docstore = None
                    if incremental:
                        docstore = DocStore(state or DocStore.default_path())
                    return await engine.run(docstore=docstore)
                except (PipelineError, ConfigError) as exc:
                    return exc

        return list(await asyncio.gather(*(_one(e) for e in engines)))

    if watch:
        async def _watch_forever() -> None:
            while True:
                results = await _run_all()
                stamp = time.strftime("%H:%M:%S")
                for config, result in zip(configs, results, strict=True):
                    if isinstance(result, BaseException):
                        typer.echo(f"[{stamp}] {config}: FAILED: {result}", err=True)
                    else:
                        typer.echo(
                            f"[{stamp}] {config}: {result.documents_ingested} documents, "
                            f"{result.chunks_indexed} chunks indexed"
                        )
                        typer.echo(
                            f"  incremental: {result.documents_changed} changed, "
                            f"{result.documents_skipped} skipped, "
                            f"{result.documents_deleted} deleted"
                        )
                await asyncio.sleep(interval)

        try:
            asyncio.run(_watch_forever())
        except KeyboardInterrupt:
            typer.echo("\nWatching stopped.")
        finally:
            if metrics_server is not None:
                metrics_server.shutdown()
        return

    try:
        results = asyncio.run(_run_all())
    finally:
        if metrics_server is not None:
            metrics_server.shutdown()
    failed = 0
    for config, result in zip(configs, results, strict=True):
        if isinstance(result, BaseException):
            failed += 1
            typer.echo(f"{config}: FAILED: {result}", err=True)
        else:
            typer.echo(
                f"{config}: {result.documents_ingested} documents, "
                f"{result.chunks_indexed} chunks indexed"
            )
            if incremental:
                typer.echo(
                    f"  incremental: {result.documents_changed} changed, "
                    f"{result.documents_skipped} skipped, "
                    f"{result.documents_deleted} deleted"
                )
            if result.embed_cache_hits or result.embed_cache_misses:
                typer.echo(
                    f"  embed cache: {result.embed_cache_hits} hits, "
                    f"{result.embed_cache_misses} misses"
                )
    if failed:
        raise typer.Exit(1)
