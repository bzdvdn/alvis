"""Pipeline YAML contract — the public, versioned configuration schema.

This is the no-code interface of Winnow: users describe a pipeline declaratively
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
    """A source adapter (filesystem, Confluence, GitHub, GitLab, S3...)."""

    type: str
    config: dict[str, Any] = Field(default_factory=dict)


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
        return int(self.config.get("max_tokens", 500))

    @property
    def overlap(self) -> int:
        return int(self.config.get("overlap", 50))

    @property
    def max_chars(self) -> int:
        return int(self.config.get("max_chars", 2000))

    @property
    def overlap_chars(self) -> int:
        return int(self.config.get("overlap_chars", 200))


class EmbedConfig(_Stage):
    """Chunks → vectors strategy."""

    type: str = "default"
    config: dict[str, Any] = Field(default_factory=dict)


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
                f"this version of winnow supports schema_version "
                f"{PIPELINE_SCHEMA_VERSION!r}"
            )
        return value