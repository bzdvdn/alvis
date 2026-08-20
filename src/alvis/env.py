"""Environment handling — `.env` loading and early validation of secrets.

Alvis configs reference secrets by *name* (``api_token_env``,
``access_key_env``, ``dsn_env`` ...) and the adapters read them from the
environment at request time. Two conveniences sit here:

- :func:`load_dotenv` — a tiny ``KEY=VALUE`` parser (quotes, comments,
  no-overwrite) so a ``.env`` in the project root is honoured without a
  dependency.
- :func:`missing_env` — walk a loaded :class:`PipelineConfig` for every
  ``*_env`` reference and report which names are absent *before* a run, so
  ``alvis run`` fails fast with "set CONFLUENCE_API_TOKEN" instead of dying
  mid-request.
"""

from __future__ import annotations

import os
from pathlib import Path

from alvis.config import PipelineConfig


def load_dotenv(path: str | Path) -> dict[str, str]:
    """Load ``KEY=VALUE`` pairs from ``path`` into ``os.environ``.

    Existing variables win (no overwrite), blank lines and ``#`` comments are
    skipped, and double-quoted values are unquoted. Returns what was loaded.
    """
    file = Path(path)
    if not file.is_file():
        return {}
    loaded: dict[str, str] = {}
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def _env_refs(payload: object) -> list[str]:
    """Every ``*_env`` string value in a (config) dict tree."""
    refs: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if (
                isinstance(key, str)
                and key.endswith("_env")
                and isinstance(value, str)
                and value
            ):
                refs.append(value)
            refs.extend(_env_refs(value))
    elif isinstance(payload, list):
        for item in payload:
            refs.extend(_env_refs(item))
    return refs


def missing_env(pipeline: PipelineConfig) -> list[str]:
    """Env var names referenced by ``pipeline`` but absent from the environment."""
    present = _env_refs(pipeline.model_dump())
    return sorted(
        {name for name in present if os.environ.get(name) in (None, "")}
    )


__all__ = ["load_dotenv", "missing_env"]
