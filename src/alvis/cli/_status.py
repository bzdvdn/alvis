"""``alvis status`` — report each pipeline's ingestion state."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import typer

from alvis.cli._shared import (
    _enable_plugins,
    _fail_missing_env,
    _load_env_file,
    _resolve_configs,
    app,
)
from alvis.config import ConfigError
from alvis.docstore import DocStore
from alvis.factories import build_indexer, build_source, source_identity
from alvis.pipeline.engine import PipelineEngine
from alvis.sources.base import SourceError


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
        help="Pipeline YAML configs (default: alvis/pipelines/*.yaml).",
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
        help="Incremental state file (default .alvis/state.json).",
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
