"""Command-line interface for Winnow."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import typer
import yaml

import winnow.observability as ob
from winnow import __version__
from winnow.answer import Synthesizer
from winnow.config import ConfigError, load_config
from winnow.core.models import CCT_SCHEMA_VERSION
from winnow.docstore import DocStore
from winnow.env import load_dotenv, missing_env
from winnow.errors import PipelineError
from winnow.factories import build_indexer, build_source, source_identity
from winnow.observability import setup_logging
from winnow.pipeline.engine import PipelineEngine, PipelineResult
from winnow.pipeline.runner import answer_async, query_async
from winnow.plugin import KINDS, Plugin, discover_plugins, load_local_plugins, registry
from winnow.registry import check_pipeline_supported
from winnow.sources.base import SourceError

app = typer.Typer(
    name="winnow",
    help="No-code framework for building corporate knowledge bases.",
    no_args_is_help=True,
)

PIPELINES_DIR = Path("winnow/pipelines")
"""Default folder scanned for pipeline YAML configs when none are given."""


def _default_configs() -> list[Path]:
    """Configs resolved when no paths are passed: ``winnow.yaml`` + ``winnow/pipelines/*.yaml``."""
    top = [Path("winnow.yaml")] if Path("winnow.yaml").is_file() else []
    return sorted([*top, *PIPELINES_DIR.glob("*.yaml")])


def _resolve_configs(configs: list[Path] | None) -> list[Path]:
    if configs:
        return configs
    found = _default_configs()
    if not found:
        raise typer.BadParameter(
            "no configs given and none in winnow/pipelines/ or ./winnow.yaml"
            " (run 'winnow init' or pass config paths)"
        )
    return found


def _enable_plugins(plugin_dirs: list[Path] | None) -> list[Plugin]:
    """Discover installed entry points, then load explicit local plugin dirs."""
    discover_plugins()
    if plugin_dirs:
        load_local_plugins(plugin_dirs)
    return list(registry().plugins())


def _load_env_file(env_file: Path | None) -> None:
    """Load an explicit ``--env-file``, or a conventional ``./.env`` when present."""
    if env_file is not None:
        load_dotenv(env_file)
    elif Path(".env").is_file():
        load_dotenv(".env")


def _fail_missing_env(pipeline: object) -> None:
    """Fail fast when the config references env vars that are not set."""
    missing = missing_env(pipeline)  # type: ignore[arg-type]
    if not missing:
        return
    typer.echo(
        "Missing environment variable(s) referenced by the config: "
        + ", ".join(missing)
        + "\nSet them in the shell or point --env-file at a .env file.",
        err=True,
    )
    raise typer.Exit(1)


_SOURCE_TEMPLATES: dict[str, dict[str, object]] = {
    "fs": {"type": "fs", "config": {"path": "./data"}},
    "confluence": {
        "type": "confluence",
        "config": {
            "url": "https://wiki.example.com",
            "space": "TEAM",
            "api_token_env": "CONFLUENCE_API_TOKEN",
        },
    },
    "github": {
        "type": "github",
        "config": {
            "repo": "org/repo",
            "branch": "main",
            "api_token_env": "GITHUB_TOKEN",
        },
    },
    "gitlab": {
        "type": "gitlab",
        "config": {
            "url": "https://gitlab.com",
            "project": "org/repo",
            "api_token_env": "GITLAB_TOKEN",
        },
    },
    "s3": {
        "type": "s3",
        "config": {
            "url": "http://localhost:9000",
            "bucket": "docs",
            "region": "us-east-1",
            "access_key_env": "S3_ACCESS_KEY",
            "secret_key_env": "S3_SECRET_KEY",
        },
    },
    "static_url": {
        "type": "static_url",
        "config": {
            "urls": ["https://example.com/docs/"],
        },
    },
}

_INDEX_TEMPLATES: dict[str, dict[str, object]] = {
    "memory": {"type": "memory", "config": {}},
    "qdrant": {
        "type": "qdrant",
        "config": {"url": "http://localhost:6333", "collection": "winnow_docs"},
    },
    "pgvector": {
        "type": "pgvector",
        "config": {"dsn_env": "POSTGRES_DSN", "table": "winnow_chunks"},
    },
}


def _pipeline_yaml(source: str, index: str) -> str:
    """Render a runnable pipeline from the ``--source`` / ``--index`` templates."""
    if source not in _SOURCE_TEMPLATES:
        raise typer.BadParameter(
            f"unknown source {source!r} (known: {sorted(_SOURCE_TEMPLATES)})"
        )
    if index not in _INDEX_TEMPLATES:
        raise typer.BadParameter(
            f"unknown index {index!r} (known: {sorted(_INDEX_TEMPLATES)})"
        )
    pipeline = {
        "pipeline": {
            "source": _SOURCE_TEMPLATES[source],
            "extract": {"strategy": "auto"},
            "chunk": {
                "strategy": "auto",
                "config": {"max_tokens": 500, "overlap": 50},
            },
            "embed": {"type": "default"},
            "index": _INDEX_TEMPLATES[index],
        }
    }
    return yaml.safe_dump(pipeline, sort_keys=False)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"winnow {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Print version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Winnow entry point."""


@app.command()
def init(
    source: str = typer.Option(  # noqa: B008
        "confluence",
        "--source",
        help="Source adapter to scaffold (fs, confluence, github, gitlab, s3, static_url).",
    ),
    index: str = typer.Option(  # noqa: B008
        "qdrant",
        "--index",
        help="Index adapter to scaffold (memory, qdrant, pgvector).",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing winnow.yaml."),
    path: Path = typer.Argument(  # noqa: B008
        Path("winnow.yaml"), help="Where to write the pipeline config."
    ),
) -> None:
    """Scaffold a new pipeline project (source/index-specific template)."""
    if path.exists() and not force:
        typer.echo(f"Error: {path} already exists. Use --force to overwrite.", err=True)
        raise typer.Exit(1)
    path.write_text(_pipeline_yaml(source, index), encoding="utf-8")
    typer.echo(f"Created {path} ({source} source, {index} index)")
    typer.echo(
        f"Next: edit {path}, set its env vars (or a .env), "
        "then run 'winnow validate {path}'"
    )


@app.command()
def plugins(
    plugin_dirs: list[Path] = typer.Option(  # noqa: B008
        None,
        "--plugins",
        help="Directories of local companion-plugin .py files to load.",
    ),
) -> None:
    """List installed plugins discovered via entry points (and --plugins dirs)."""
    installed = _enable_plugins(plugin_dirs)
    if not installed:
        typer.echo("No plugins discovered (entry-point group: winnow.plugins).")
        return
    for plugin in installed:
        typer.echo(f"{plugin.name} {plugin.version} — {plugin.summary or 'no summary'}")
        for kind in KINDS:
            provided = plugin.provided_types(kind)
            if provided:
                typer.echo(f"  {kind}: {', '.join(sorted(provided))}")


@app.command()
def validate(
    config: str = typer.Argument(..., help="Path to the pipeline YAML config."),  # noqa: B008
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
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Emit a machine-readable validation report as JSON.",
    ),
) -> None:
    """Validate the pipeline YAML config and print a report."""
    _enable_plugins(plugin_dirs)
    _load_env_file(env_file)
    try:
        pipeline = load_config(config)
    except ConfigError as exc:
        if json_output:
            typer.echo(json.dumps({"valid": False, "error": str(exc)}))
        else:
            typer.echo(f"Invalid config:\n{exc}", err=True)
        raise typer.Exit(1) from exc

    missing = missing_env(pipeline)
    problems = check_pipeline_supported(
        source=pipeline.source.type,
        extract=pipeline.extract.strategy,
        chunk=pipeline.chunk.strategy,
        embed=pipeline.embed.type,
        index=pipeline.index.type if pipeline.index else None,
    ) + [f"missing environment variable: {name}" for name in missing]

    engine = PipelineEngine(pipeline)
    stages: dict[str, str] = {
        "source": engine._source_summary(),
        "extract": pipeline.extract.strategy,
        "chunk": engine._chunk_summary(),
        "embed": engine._embed_summary(),
        "index": engine._index_summary(),
    }
    graph = engine.describe_graph()
    report = {
        "valid": not problems,
        "path": config,
        "contract": {
            "schema_version": pipeline.schema_version,
            "cct": CCT_SCHEMA_VERSION,
        },
        "stages": stages,
        "problems": problems,
        "graph": graph,
    }

    if json_output:
        typer.echo(json.dumps(report, indent=2))
        if problems:
            raise typer.Exit(1)
        return

    if problems:
        typer.echo("Invalid config:\n" + "\n".join(f"  {p}" for p in problems), err=True)
        raise typer.Exit(1)

    typer.echo(f"{config} is valid:")
    typer.echo(
        f"  contract: schema {pipeline.schema_version} / cct {CCT_SCHEMA_VERSION}"
    )
    typer.echo("\n  Stages")
    typer.echo(f"    source: {stages['source']}")
    typer.echo(f"    extract: {stages['extract']}")
    typer.echo(f"    chunk: {stages['chunk']}")
    typer.echo(f"    embed: {stages['embed']}")
    typer.echo(f"    index: {stages['index']}")
    typer.echo("\n  Pipeline graph")
    typer.echo("\n".join(f"    {line}" for line in graph.splitlines()))


@app.command()
def run(
    configs: list[Path] = typer.Argument(  # noqa: B008
        None,
        help="Paths to pipeline YAML configs. Default: winnow/pipelines/*.yaml "
        "plus ./winnow.yaml. Multiple paths run in parallel.",
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
        help="Incremental state file (default .winnow/state.json). "
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


@app.command()
def query(
    config: str = typer.Argument(  # noqa: B008
        ...,
        help="Path to the pipeline YAML config (uses its embed + index stages).",
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
    text: str = typer.Option(  # noqa: B008
        ...,
        "--text",
        "-t",
        help="Query text to embed and search for.",
    ),
    top_k: int = typer.Option(  # noqa: B008
        5,
        "--top-k",
        help="Number of nearest chunks to return.",
    ),
    answer: bool = typer.Option(  # noqa: B008
        False,
        "--answer",
        "-a",
        help="Synthesize a cited answer over the hits (LLM optional).",
    ),
    llm_base_url: str = typer.Option(  # noqa: B008
        "https://api.openai.com/v1",
        "--answer-base-url",
        help="OpenAI-compatible chat endpoint for --answer.",
    ),
    llm_model: str = typer.Option(  # noqa: B008
        "gpt-4o-mini",
        "--answer-model",
        help="Chat model for --answer.",
    ),
    llm_api_token_env: str = typer.Option(  # noqa: B008
        "OPENAI_API_KEY",
        "--answer-api-token-env",
        help="Env var holding the chat API token for --answer.",
    ),
) -> None:
    """Retrieve the chunks closest to --text (or synthesize an answer)."""
    _enable_plugins(plugin_dirs)
    _load_env_file(env_file)
    try:
        if answer:
            llm = None
            if os.environ.get(llm_api_token_env):
                llm = Synthesizer(
                    base_url=llm_base_url,
                    model=llm_model,
                    api_token_env=llm_api_token_env,
                )
            result = asyncio.run(answer_async(config, text, top_k=top_k, llm=llm))
        else:
            results = asyncio.run(query_async(config, text, top_k=top_k))
            result = None
    except (ConfigError, PipelineError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if result is not None:
        typer.echo(result.text)
        typer.echo("")
        if result.citations:
            typer.echo("Sources:")
            for citation in result.citations:
                typer.echo(
                    f"  [{citation.index}] {citation.source_uri}  "
                    f"(score {citation.score:.4f})"
                )
        return
    if not results:
        typer.echo("No matches found.")
        return
    for index, hit in enumerate(results, start=1):
        typer.echo(f"[{index}] {hit.score:.4f}  {hit.source_uri}")
        for key, value in hit.metadata.items():
            if value:
                typer.echo(f"      {key}: {value}")
        snippet = " ".join(hit.text.split())
        typer.echo(f"      {snippet[:180]}")


async def _collect_status(
    path: Path,
    engine: PipelineEngine,
    store: DocStore,
    *,
    probe: bool,
) -> dict[str, object]:
    """Report for one pipeline: source health, docstore state, last run, index."""
    cfg = engine.config
    source_id = source_identity(cfg.source)
    report: dict[str, object] = {
        "path": str(path),
        "source": cfg.source.type,
        "index": engine._index_summary(),
        "state": str(store.path),
    }

    try:
        source = build_source(cfg.source, max_bytes=cfg.extract.max_bytes)
        listing = getattr(source, "list_documents", None)
        if listing is None:
            report["source_health"] = None
        else:
            metas = await listing()
            report["source_health"] = {"ok": True, "documents": len(metas)}
    except SourceError as exc:
        report["source_health"] = {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — a failed probe must not kill status
        report["source_health"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    known = 0
    for candidate, entry in store.sources():
        if candidate == source_id:
            documents = entry.get("documents")
            known = len(documents) if isinstance(documents, dict) else 0
    report["documents_known"] = known

    run = store.last_run(source_id)
    if run is None:
        report["last_run"] = None
    else:
        report["last_run"] = {
            "at": run.at,
            "ok": run.ok,
            "error": run.error,
            "duration_seconds": round(run.duration_seconds, 3),
            "documents": run.documents,
            "chunks": run.chunks,
            "changed": run.changed,
            "skipped": run.skipped,
            "deleted": run.deleted,
            "embed_cache_hits": run.embed_cache_hits,
            "embed_cache_misses": run.embed_cache_misses,
        }

    if cfg.index is not None:
        try:
            indexer = build_indexer(cfg.index)
            counter = getattr(indexer, "count", None)
            if isinstance(counter, int):
                report["index_points"] = counter
            elif callable(counter):
                if not probe:
                    report["index_points"] = "remote (pass --probe to check)"
                else:
                    try:
                        value = await asyncio.wait_for(counter(), timeout=5.0)
                        report["index_points"] = int(value)
                    except Exception:  # noqa: BLE001
                        report["index_points"] = "unreachable"
            else:
                report["index_points"] = "n/a"
        except (SourceError, ValueError) as exc:
            report["index_points"] = f"error: {exc}"
    else:
        report["index_points"] = "n/a"
    return report


@app.command()
def status(
    configs: list[Path] = typer.Argument(  # noqa: B008
        None,
        help="Pipeline YAML configs (default: winnow/pipelines/*.yaml).",
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
    state: Path | None = typer.Option(  # noqa: B008
        None,
        "--state",
        help="Incremental state file (default .winnow/state.json).",
    ),
    probe: bool = typer.Option(  # noqa: B008
        False,
        "--probe",
        help="Check the remote index live (may take a few seconds).",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Emit machine-readable reports as JSON.",
    ),
) -> None:
    """Report each pipeline's ingestion state: source health, documents, last run."""
    _enable_plugins(plugin_dirs)
    _load_env_file(env_file)
    configs = _resolve_configs(configs)
    store = DocStore(state or DocStore.default_path())
    reports: list[dict[str, Any]] = []

    async def _collect_all() -> None:
        for path in configs:
            try:
                engine = PipelineEngine.from_yaml(path)
            except ConfigError as exc:
                reports.append({"path": str(path), "config_error": str(exc)})
                continue
            _fail_missing_env(engine.config)
            reports.append(await _collect_status(path, engine, store, probe=probe))

    asyncio.run(_collect_all())

    if json_output:
        typer.echo(json.dumps(reports, indent=2))
        return

    for report in reports:
        if "config_error" in report:
            typer.echo(f"{report['path']}: invalid config: {report['config_error']}")
            continue
        typer.echo(
            f"{report['path']}: {report['source']} source → {report['index']}"
        )
        health = report["source_health"]
        if health is None:
            typer.echo("  source health: not probeable (source has no listing)")
        elif health["ok"]:
            typer.echo(f"  source health: OK ({health['documents']} documents)")
        else:
            typer.echo(f"  source health: FAILED: {health['error']}")
        typer.echo(f"  documents known: {report['documents_known']}")
        run = report["last_run"]
        if run is None:
            typer.echo("  last run: never")
        else:
            outcome = "ok" if run["ok"] else f"FAILED: {run['error']}"
            typer.echo(
                f"  last run: {run['at']} ({outcome}, {run['duration_seconds']:.2f}s)"
            )
            typer.echo(
                f"    documents {run['documents']} | chunks {run['chunks']} | "
                f"changed {run['changed']} | skipped {run['skipped']} | "
                f"deleted {run['deleted']}"
            )
            typer.echo(
                f"    embed cache: {run['embed_cache_hits']} hits / "
                f"{run['embed_cache_misses']} misses"
            )
        typer.echo(f"  index points: {report['index_points']}")


class _MetricsHandler(BaseHTTPRequestHandler):
    """Serves the process-wide metric store as Prometheus text exposition."""

    def do_GET(self) -> None:
        if self.path.rstrip("/") != "/metrics":
            self.send_error(404)
            return
        store = ob.get_metrics()
        try:
            body = store.export_prometheus()
        except Exception:  # noqa: BLE001 — prometheus_client missing
            body = b""
        if not body:
            body = store.render_plain_text()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        ob.LOG.debug(
            "metrics.http",
            extra={"message": " ".join((format, *map(str, args)))},
        )


def _start_metrics_server(
    host: str | None,
    port: int | None,
) -> ThreadingHTTPServer | None:
    """Start /metrics on a daemon thread, or ``None`` when ``port`` is unset."""
    if port is None:
        return None
    server = ThreadingHTTPServer((host or "127.0.0.1", port), _MetricsHandler)

    def _serve() -> None:
        server.serve_forever()

    threading.Thread(target=_serve, daemon=True).start()
    typer.echo(f"Serving /metrics on http://{host or '127.0.0.1'}:{port}")
    return server


@app.command()
def metrics(
    host: str = typer.Option(  # noqa: B008
        "127.0.0.1",
        "--host",
        help="Interface to bind (use 0.0.0.0 inside containers).",
    ),
    port: int = typer.Option(  # noqa: B008
        8000,
        "--port",
        help="TCP port to serve /metrics on.",
    ),
) -> None:
    """Expose Prometheus metrics over HTTP (GET /metrics)."""
    server = _start_metrics_server(host, port)
    if server is None:
        raise typer.Exit(1)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


if __name__ == "__main__":
    app()