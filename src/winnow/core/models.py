"""Canonical Content Tree models.

Stage contract (v0, provisional — hardens toward v1.0):
    Source  →  Artifact  →  Canonical Content Tree  →  Chunk  →  Embedding  →  Index
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceConfig:
    """Declarative description of a source adapter."""

    type: str
    config: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkConfig:
    """Declarative description of a chunking strategy."""

    strategy: str = "auto"
    max_tokens: int = 500
    overlap: int = 50


@dataclass(frozen=True)
class PipelineConfig:
    """Top-level pipeline definition, mirroring the YAML contract."""

    source: SourceConfig
    extract: object | None = None
    chunk: ChunkConfig = field(default_factory=ChunkConfig)
    embed: object | None = None
    index: object | None = None


@dataclass(frozen=True)
class Artifact:
    """A raw snapshot of a single document obtained from a source."""

    step_id: str
    uri: str
    content_type: str
    data: bytes
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    """A piece of the canonical content ready for embedding."""

    text: str
    source_uri: str
    metadata: dict[str, object] = field(default_factory=dict)


def content_hash(artifact: Artifact) -> str:
    """Deterministic content hash used for dedup (v0.2+)."""
    import hashlib

    return hashlib.sha256(artifact.data).hexdigest()