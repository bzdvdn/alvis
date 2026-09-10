"""Command-line interface for Alvis.

One module per subcommand (``_init.py``, ``_run.py``, ``_query.py``, ...),
all registering onto the single :data:`alvis.cli._shared.app` Typer
instance re-exported here as ``app``. Import order below only needs to
happen once, at import time, so each ``@app.command()`` decorator runs.
"""

from __future__ import annotations

import typer

from alvis import __version__

# Each import registers its module's command(s) onto `app` as a side effect.
from alvis.cli import (  # noqa: E402, F401
    _eval,
    _init,
    _metrics,
    _plugins,
    _query,
    _run,
    _status,
    _validate,
)
from alvis.cli._metrics_server import _start_metrics_server  # noqa: E402, F401
from alvis.cli._shared import app
from alvis.secrets import configure_from_env


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"alvis {__version__}")
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
    """Alvis entry point."""
    configure_from_env()


__all__ = ["app", "main"]
