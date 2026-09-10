"""Shared Typer app instance and helpers used across CLI subcommand modules.

Kept separate from ``alvis/cli/__init__.py`` so subcommand modules
(``_run.py``, ``_query.py``, ...) can ``from alvis.cli._shared import app``
without a circular import back through the package's own ``__init__.py``.
"""

from __future__ import annotations

from pathlib import Path

import typer

from alvis.env import load_dotenv, missing_env
from alvis.plugin import Plugin, discover_plugins, load_local_plugins, registry

app = typer.Typer(
    name="alvis",
    help="No-code framework for building corporate knowledge bases.",
    no_args_is_help=True,
)

PIPELINES_DIR = Path("alvis/pipelines")
"""Default folder scanned for pipeline YAML configs when none are given."""


def _default_configs() -> list[Path]:
    """Configs resolved when no paths are passed: ``alvis.yaml`` + ``alvis/pipelines/*.yaml``."""
    top = [Path("alvis.yaml")] if Path("alvis.yaml").is_file() else []
    return sorted([*top, *PIPELINES_DIR.glob("*.yaml")])


def _resolve_configs(configs: list[Path] | None) -> list[Path]:
    if configs:
        return configs
    found = _default_configs()
    if not found:
        raise typer.BadParameter(
            "no configs given and none in alvis/pipelines/ or ./alvis.yaml"
            " (run 'alvis init' or pass config paths)"
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


__all__ = [
    "PIPELINES_DIR",
    "app",
    "_default_configs",
    "_enable_plugins",
    "_fail_missing_env",
    "_load_env_file",
    "_resolve_configs",
]
