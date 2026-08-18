"""Command-line interface for Winnow."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from winnow import __version__
from winnow.config import ConfigError, load_config
from winnow.pipeline.engine import PipelineEngine
from winnow.registry import check_pipeline_supported
from winnow.sources.base import SourceError

app = typer.Typer(
    name="winnow",
    help="No-code framework for building corporate knowledge bases.",
    no_args_is_help=True,
)

_DEFAULT_PIPELINE = """\
pipeline:
  source:
    type: confluence
    config:
      url: https://wiki.example.com
      space: TEAM
      api_token_env: CONFLUENCE_API_TOKEN

  extract:
    strategy: auto

  chunk:
    strategy: auto
    config:
      max_tokens: 500
      overlap: 50

  embed:
    type: default
    config:
      model: text-embedding-3-small

  index:
    type: qdrant
    config:
      url: http://localhost:6333
      collection: winnow_docs
"""


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
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing winnow.yaml."),
    path: Path = typer.Argument(  # noqa: B008
        Path("winnow.yaml"), help="Where to write the pipeline config."
    ),
) -> None:
    """Scaffold a new pipeline project."""
    if path.exists() and not force:
        typer.echo(f"Error: {path} already exists. Use --force to overwrite.", err=True)
        raise typer.Exit(1)
    path.write_text(_DEFAULT_PIPELINE, encoding="utf-8")
    typer.echo(f"Created {path}")
    typer.echo(f"Next: edit {path}, then run 'winnow validate {path}'")


@app.command()
def validate(config: str = typer.Argument(..., help="Path to the pipeline YAML config.")) -> None:  # noqa: B008
    """Validate the pipeline YAML config."""
    try:
        pipeline = load_config(config)
    except ConfigError as exc:
        typer.echo(f"Invalid config:\n{exc}", err=True)
        raise typer.Exit(1) from exc

    problems = check_pipeline_supported(
        source=pipeline.source.type,
        extract=pipeline.extract.strategy,
        chunk=pipeline.chunk.strategy,
        embed=pipeline.embed.type,
        index=pipeline.index.type if pipeline.index else None,
    )
    if problems:
        typer.echo("Invalid config:\n" + "\n".join(f"  {p}" for p in problems), err=True)
        raise typer.Exit(1)

    typer.echo(f"{config} is valid:")
    typer.echo(f"  source: {pipeline.source.type}")
    typer.echo(f"  extract: {pipeline.extract.strategy}")
    typer.echo(f"  chunk: {pipeline.chunk.strategy} (max_tokens={pipeline.chunk.max_tokens})")
    typer.echo(f"  embed: {pipeline.embed.type}")
    index_type = pipeline.index.type if pipeline.index else "not configured"
    typer.echo(f"  index: {index_type}")


@app.command()
def run(config: str = typer.Argument(..., help="Path to the pipeline YAML config.")) -> None:
    """Run the configured ingestion pipeline."""
    try:
        engine = PipelineEngine.from_yaml(config)
    except ConfigError as exc:
        typer.echo(f"Invalid config:\n{exc}", err=True)
        raise typer.Exit(1) from exc

    try:
        result = asyncio.run(engine.run())
    except (ConfigError, SourceError) as exc:
        typer.echo(f"Cannot run pipeline:\n{exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Pipeline finished: {result.documents_ingested} documents, "
               f"{result.chunks_indexed} chunks indexed")


if __name__ == "__main__":
    app()