"""Command-line interface for Winnow."""

import typer

from winnow import __version__

app = typer.Typer(
    name="winnow",
    help="No-code framework for building corporate knowledge bases.",
    no_args_is_help=True,
)


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
def init() -> None:
    """Scaffold a new pipeline project."""
    typer.echo("Not implemented yet: will scaffold a pipeline project.")


@app.command()
def run(config: str = typer.Argument(..., help="Path to the pipeline YAML config.")) -> None:
    """Run the configured ingestion pipeline."""
    typer.echo(f"Not implemented yet: will run pipeline from {config}")


@app.command()
def validate(config: str = typer.Argument(..., help="Path to the pipeline YAML config.")) -> None:
    """Validate the pipeline YAML config."""
    typer.echo(f"Not implemented yet: will validate {config}")


if __name__ == "__main__":
    app()