"""Pipeline YAML contract — the public, versioned configuration schema.

This is the no-code interface of Winnow: users describe a pipeline declaratively
and the engine executes it. Any change here is a contract change (RFC-first,
see CONSTITUTION.md).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Stage(BaseModel):
    """Base for stage config blocks."""

    model_config = ConfigDict(extra="forbid")


class SourceConfig(_Stage):
    """A source adapter (Confluence, GitLab, S3...)."""

    type: str
    config: dict[str, Any] = Field(default_factory=dict)


class ExtractConfig(_Stage):
    """Artifact → Canonical Content Tree strategy."""

    strategy: str = "auto"


class ChunkConfig(_Stage):
    """Canonical Content Tree → chunks strategy."""

    strategy: str = "auto"
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("config")
    @classmethod
    def _validate_chunk_settings(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key, minimum in (("max_tokens", 1), ("overlap", 0)):
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


class EmbedConfig(_Stage):
    """Chunks → vectors strategy."""

    type: str = "default"
    config: dict[str, Any] = Field(default_factory=dict)


class IndexConfig(_Stage):
    """Vectors → vector store strategy."""

    type: str
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineConfig(_Stage):
    """Top-level pipeline definition, mirroring the YAML contract."""

    source: SourceConfig
    extract: ExtractConfig = Field(default_factory=ExtractConfig)
    chunk: ChunkConfig = Field(default_factory=ChunkConfig)
    embed: EmbedConfig = Field(default_factory=EmbedConfig)
    index: IndexConfig | None = None