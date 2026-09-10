"""``alvis validate`` — check a pipeline YAML config and print a report."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from alvis.cli._shared import _enable_plugins, _load_env_file, app
from alvis.config import ConfigError, load_config
from alvis.core.models import CCT_SCHEMA_VERSION
from alvis.env import missing_env
from alvis.pipeline.engine import PipelineEngine
from alvis.registry import check_pipeline_supported


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
