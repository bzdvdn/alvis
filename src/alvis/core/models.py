"""Canonical Content Tree runtime models.

Stage contract (v1, frozen as of v1.0 — see ``docs/schema.md`` for the
backward-compatible evolution policy):
    Source  →  Artifact  →  Canonical Content Tree  →  Chunk  →  Embedding  →  Index

The public YAML config lives in `alvis.config`; these are the runtime objects
that flow through the pipeline. Models are pydantic (frozen) so the contract
is validated, serializable, and versionable. Every ``Document`` carries its
``schema_version`` so consumers can detect which contract it conforms to.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CCT_SCHEMA_VERSION = 1
"""Current Canonical Content Tree schema version (bumped only by RFC)."""


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
    """Canonical Content Tree v1 — the intermediate form between extraction and chunking."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = CCT_SCHEMA_VERSION
    uri: str
    title: str
    sections: tuple[Section, ...] = Field(default_factory=tuple)


class SearchHit(BaseModel):
    """A retrieval result: a stored chunk matched against a query vector."""

    model_config = ConfigDict(frozen=True)

    text: str
    source_uri: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float


class DocumentMeta(BaseModel):
    """Cheap listing metadata of a document, before its body is fetched.

    ``fingerprint`` identifies the document's current state from listing data
    alone (S3 ETag, GitLab/GitHub blob sha, Confluence version, local file
    hash). Incremental ingestion compares it against the docstore to skip
    downloading unchanged documents.
    """

    model_config = ConfigDict(frozen=True)

    uri: str
    step_id: str
    fingerprint: str
    content_type: str | None = None


def content_hash(artifact: Artifact) -> str:
    """Deterministic content hash used for dedup (v0.2+)."""
    return hashlib.sha256(artifact.data).hexdigest()