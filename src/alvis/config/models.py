"""Pipeline YAML contract — the public, versioned configuration schema.

This is the no-code interface of Alvis: users describe a pipeline declaratively
and the engine executes it. The schema is versioned (``schema_version``,
default 1); any change here is a contract change (RFC-first, see
CONSTITUTION.md and docs/schema.md for the evolution policy).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

PIPELINE_SCHEMA_VERSION = 1


class _Stage(BaseModel):
    """Base for stage config blocks."""

    model_config = ConfigDict(extra="forbid")


class SourceConfig(_Stage):
    """A source adapter (filesystem, Confluence, GitHub, GitLab, S3...).

    ``config.acl`` (optional, additive as of schema 1) is a reserved key:
    a static list of principal strings stamped onto every artifact this
    source fetches, promoted to the ``__acl`` system field at index time
    (see ``docs/schema.md``). It is not a source constructor argument.
    """

    type: str
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("config")
    @classmethod
    def _validate_acl(cls, value: dict[str, Any]) -> dict[str, Any]:
        acl = value.get("acl")
        if acl is None:
            return value
        if not isinstance(acl, list) or not all(isinstance(item, str) for item in acl):
            raise ValueError("source config 'acl' must be a list of strings")
        return value


class ExtractConfig(_Stage):
    """Artifact → Canonical Content Tree strategy."""

    strategy: str = "auto"
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("config")
    @classmethod
    def _validate_extract_settings(cls, value: dict[str, Any]) -> dict[str, Any]:
        max_bytes = value.get("max_bytes")
        if max_bytes is not None and (
            not isinstance(max_bytes, int) or max_bytes < 1
        ):
            raise ValueError("extract config 'max_bytes' must be an integer >= 1")
        return value

    @property
    def max_bytes(self) -> int | None:
        """Optional per-artifact byte budget for extraction (None = no cap)."""
        return self.config.get("max_bytes")


class ChunkConfig(_Stage):
    """Canonical Content Tree → chunks strategy."""

    strategy: str = "auto"
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("config")
    @classmethod
    def _validate_chunk_settings(cls, value: dict[str, Any]) -> dict[str, Any]:
        limits = (("max_tokens", 1), ("overlap", 0), ("max_chars", 1), ("overlap_chars", 0))
        for key, minimum in limits:
            setting = value.get(key)
            if setting is not None and not isinstance(setting, int):
                raise ValueError(f"chunk config {key!r} must be an integer")
            if isinstance(setting, int) and setting < minimum:
                raise ValueError(f"chunk config {key!r} must be >= {minimum}")
        return value

    @property
    def max_tokens(self) -> int:
        """Token budget per chunk (approximated as ``len(text) // 4``)."""
        return int(self.config.get("max_tokens", 500))

    @property
    def overlap(self) -> int:
        """Trailing tokens carried between consecutive chunks."""
        return int(self.config.get("overlap", 50))

    @property
    def max_chars(self) -> int:
        """Character budget for the ``size`` chunk strategy."""
        return int(self.config.get("max_chars", 2000))

    @property
    def overlap_chars(self) -> int:
        """Character overlap between ``size`` chunks."""
        return int(self.config.get("overlap_chars", 200))


class EmbedConfig(_Stage):
    """Chunks → vectors strategy.

    ``cache`` (optional, additive as of schema 1) enables embedding reuse:
    ``true`` keeps vectors in memory for the process; a dict with ``path``
    persists them to disk so unchanged chunks are not re-embedded on later
    runs. Cache keys embed the model signature, so changing the model never
    serves stale vectors.
    """

    type: str = "default"
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("config")
    @classmethod
    def _validate_embed_settings(cls, value: dict[str, Any]) -> dict[str, Any]:
        cache = value.get("cache")
        if cache is None or cache is True or cache is False:
            return value
        if isinstance(cache, dict):
            unknown = set(cache) - {"path"}
            if unknown:
                raise ValueError(
                    f"embed config 'cache' has unknown keys: {sorted(unknown)}"
                )
            path = cache.get("path")
            if path is not None and not isinstance(path, str):
                raise ValueError("embed config 'cache.path' must be a string")
            return value
        raise ValueError(
            "embed config 'cache' must be a bool or a dict with an optional 'path'"
        )

    @property
    def cache_enabled(self) -> bool:
        """Whether embedding reuse is configured."""
        cache = self.config.get("cache")
        if cache is None:
            return False
        if isinstance(cache, bool):
            return cache
        return True

    @property
    def cache_path(self) -> str | None:
        """On-disk cache location, or ``None`` for an in-memory cache."""
        cache = self.config.get("cache")
        if isinstance(cache, dict):
            return cache.get("path")
        return None


class IndexConfig(_Stage):
    """Vectors → vector store strategy."""

    type: str
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineConfig(_Stage):
    """Top-level pipeline definition, mirroring the YAML contract.

    ``schema_version`` identifies which version of the YAML schema the file
    conforms to; only the current version (1) is supported, so future
    incompatible files fail fast instead of being misread.
    """

    schema_version: int = PIPELINE_SCHEMA_VERSION
    source: SourceConfig
    extract: ExtractConfig = Field(default_factory=ExtractConfig)
    chunk: ChunkConfig = Field(default_factory=ChunkConfig)
    embed: EmbedConfig = Field(default_factory=EmbedConfig)
    index: IndexConfig | None = None

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: int) -> int:
        if value != PIPELINE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version {value!r}; "
                f"this version of alvis supports schema_version "
                f"{PIPELINE_SCHEMA_VERSION!r}"
            )
        return value