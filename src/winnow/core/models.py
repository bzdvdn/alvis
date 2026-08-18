"""Canonical Content Tree runtime models.

Stage contract (v0, provisional — hardens toward v1.0):
    Source  →  Artifact  →  Canonical Content Tree  →  Chunk  →  Embedding  →  Index

The public YAML config lives in `winnow.config`; these are the runtime objects
that flow through the pipeline. Models are pydantic (frozen) so the contract
is validated, serializable, and versionable.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Artifact(BaseModel):
    """A raw snapshot of a single document obtained from a source."""

    model_config = ConfigDict(frozen=True)

    step_id: str
    uri: str
    content_type: str
    data: bytes
    metadata: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    """A piece of the canonical content ready for embedding."""

    model_config = ConfigDict(frozen=True)

    text: str
    source_uri: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Section(BaseModel):
    """A heading-anchored block of the Canonical Content Tree."""

    model_config = ConfigDict(frozen=True)

    heading: str
    body: str


class Document(BaseModel):
    """Canonical Content Tree v0 — the intermediate form between extraction and chunking."""

    model_config = ConfigDict(frozen=True)

    uri: str
    title: str
    sections: tuple[Section, ...] = Field(default_factory=tuple)


def content_hash(artifact: Artifact) -> str:
    """Deterministic content hash used for dedup (v0.2+)."""
    return hashlib.sha256(artifact.data).hexdigest()