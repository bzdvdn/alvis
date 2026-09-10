"""``alvis plugins`` — list installed plugins discovered via entry points."""

from __future__ import annotations

from pathlib import Path

import typer

from alvis.cli._shared import _enable_plugins, app
from alvis.plugin import KINDS


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
        typer.echo("No plugins discovered (entry-point group: alvis.plugins).")
        return
    for plugin in installed:
        typer.echo(f"{plugin.name} {plugin.version} — {plugin.summary or 'no summary'}")
        for kind in KINDS:
            provided = plugin.provided_types(kind)
            if provided:
                typer.echo(f"  {kind}: {', '.join(sorted(provided))}")
