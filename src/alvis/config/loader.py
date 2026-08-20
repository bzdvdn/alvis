"""Loading and validation of pipeline YAML configs."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from alvis.config.models import PipelineConfig


class ConfigError(Exception):
    """Raised when a pipeline config cannot be loaded or is invalid."""


def load_config(path: str | Path) -> PipelineConfig:
    """Load and validate a pipeline YAML file.

    Raises:
        ConfigError: if the file is missing, malformed YAML, or violates
                     the pipeline contract.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, dict) or "pipeline" not in raw:
        raise ConfigError(f"{config_path}: missing top-level 'pipeline' key")

    try:
        return PipelineConfig.model_validate(raw["pipeline"])
    except ValidationError as exc:
        raise ConfigError(_format_validation_errors(exc)) from exc


def _format_validation_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"])
        lines.append(f"  pipeline.{loc}: {err['msg']}")
    return "\n".join(lines)