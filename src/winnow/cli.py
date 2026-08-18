"""Command-line interface for Winnow."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from winnow import __version__
from winnow.config import ConfigError, load_config
from winnow.errors import PipelineError
from winnow.pipeline.engine import PipelineEngine, PipelineResult
from winnow.registry import check_pipeline_supported

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
def run(
    configs: list[Path] = typer.Argument(  # noqa: B008
        ...,
        help="Paths to pipeline YAML configs. Multiple paths run in parallel.",
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
) -> None:
    """Run the configured ingestion pipeline(s)."""
    if parallel < 1:
        typer.echo("Error: --parallel must be >= 1", err=True)
        raise typer.Exit(2)

    engines: list[PipelineEngine] = []
    for config in configs:
        try:
            engines.append(PipelineEngine.from_yaml(config))
        except ConfigError as exc:
            typer.echo(f"Invalid config {config}:\n{exc}", err=True)
            raise typer.Exit(1) from exc

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
        if invalid:
            raise typer.Exit(1)
        return

    async def _run_all() -> list[PipelineResult | BaseException]:
        semaphore = asyncio.Semaphore(parallel)

        async def _one(engine: PipelineEngine) -> PipelineResult | BaseException:
            async with semaphore:
                try:
                    return await engine.run()
                except (PipelineError, ConfigError) as exc:
                    return exc

        return list(await asyncio.gather(*(_one(e) for e in engines)))

    results = asyncio.run(_run_all())
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
    if failed:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()