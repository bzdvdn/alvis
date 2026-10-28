"""Document-provider adapter — turn simple content providers into a source.

Most connectors differ only in *where* a document comes from: a checkout, an
API, a database. They all return the same thing — a stable id, a title, the
body, and a few provenance fields. Implementing the full :class:`Source`/
``ListingSource`` contract for each of them means repeating the same
fingerprinting, ``Artifact``/``DocumentMeta`` construction, URI filtering, and
error wrapping.

This module keeps that glue in one place:

- :class:`SourceDocument` — the document a provider returns.
- :class:`DocumentProvider` — the one-method protocol a provider implements.
- :class:`ProviderSource` — an alvis ``ListingSource`` over one or more
  providers. It fingerprints documents by content, exposes stable
  ``documentId``/``sourceUrl``/``title`` metadata, and fails fast with
  ``SourceError`` so a partial provider listing can never be reconciled as
  "the documents were deleted".

A provider is deliberately simpler than a source: it returns content, not
bytes-on-the-wire. For connectors that can fingerprint without reading the
body (S3 ETag, GitLab blob sha, Confluence version), implement ``ListingSource``
directly instead — that avoids downloading unchanged documents entirely.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from alvis._lifecycle import aclose_quietly
from alvis.core.models import Artifact, DocumentMeta
from alvis.sources.base import SourceError


class SourceDocument(BaseModel):
    """One document returned by a :class:`DocumentProvider`.

    ``id`` is the stable identity (a page id, a repository path, a URL). It is
    preserved as ``artifact.step_id`` and ``metadata["documentId"]`` so the
    indexed chunk can be cited and read back later. ``uri`` is the
    human-facing locator (usually a URL); when omitted, ``id`` is used.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    content: bytes | str
    title: str = ""
    uri: str | None = None
    content_type: str = "text/plain"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def data(self) -> bytes:
        """The document body as bytes (``Artifact.data``'s contract)."""
        return self.content.encode("utf-8") if isinstance(self.content, str) else self.content

    @property
    def locator(self) -> str:
        """The URI used for the artifact and incremental-state keys."""
        return self.uri or self.id


@runtime_checkable
class DocumentProvider(Protocol):
    """A source of :class:`SourceDocument` values.

    Implementations should be side-effect free and return the same documents
    in the same order for the same underlying state. Any exception is turned
    into a ``SourceError`` by :class:`ProviderSource`, aborting the run before
    reconcile.
    """

    async def documents(self) -> Sequence[SourceDocument]: ...


class ProviderSource:
    """An alvis source backed by one or more :class:`DocumentProvider` objects.

    Documents are collected once per run, fingerprinted by content, and cached
    for the subsequent ``fetch(uris=...)`` call. Duplicate ids across
    providers are rejected: two documents sharing an id would fight over the
    same incremental/docstore entry.

    ``max_bytes`` mirrors the engine cap: documents whose body exceeds it are
    skipped (not fetched/ingested), matching the built-in filesystem source.
    """

    def __init__(
        self,
        providers: DocumentProvider | Sequence[DocumentProvider],
        *,
        name: str = "provider",
        max_bytes: int | None = None,
    ) -> None:
        provider_list = _as_provider_list(providers)
        if not provider_list:
            raise ValueError("ProviderSource requires at least one provider")
        self.providers: list[DocumentProvider] = provider_list
        self.name = name
        self.max_bytes = max_bytes
        self._documents: list[SourceDocument] | None = None

    async def aclose(self) -> None:
        """Close every provider that owns an async resource (best effort)."""
        for provider in self.providers:
            await aclose_quietly(provider)

    async def list_documents(self) -> list[DocumentMeta]:
        """Fingerprint every document from its content (no separate fetch)."""
        documents = await self._all()
        return [
            DocumentMeta(
                uri=document.locator,
                step_id=document.id,
                fingerprint=_fingerprint(document),
                content_type=document.content_type,
            )
            for document in documents
        ]

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        """Build artifacts for all documents, or only the wanted URIs."""
        documents = await self._all()
        artifacts = [_artifact(document, source_name=self.name) for document in documents]
        if uris is None:
            return artifacts
        return [artifact for artifact in artifacts if artifact.uri in uris]

    async def _all(self) -> list[SourceDocument]:
        if self._documents is not None:
            return self._documents

        documents: list[SourceDocument] = []
        seen: set[str] = set()
        for provider in self.providers:
            try:
                batch = list(await provider.documents())
            except Exception as exc:  # noqa: BLE001 — surface, never prune
                label = getattr(provider, "name", type(provider).__name__)
                raise SourceError(
                    f"{self.name}: provider {label} failed: {exc}"
                ) from exc
            for document in batch:
                if document.id in seen:
                    raise SourceError(
                        f"{self.name}: duplicate document id {document.id!r} "
                        f"from provider {getattr(provider, 'name', type(provider).__name__)}"
                    )
                if self.max_bytes is not None and len(document.data) > self.max_bytes:
                    continue
                seen.add(document.id)
                documents.append(document)
        self._documents = documents
        return documents


def _as_provider_list(
    providers: DocumentProvider | Sequence[DocumentProvider],
) -> list[DocumentProvider]:
    """Normalise the single-provider / sequence-of-providers union."""
    if isinstance(providers, (str, bytes)):
        raise TypeError("providers must be a DocumentProvider or a sequence of them")
    if isinstance(providers, Sequence):
        return [provider for provider in providers]
    return [providers]


def _fingerprint(document: SourceDocument) -> str:
    """Fingerprint of identity + rendered content, intentionally not metadata.

    Provider metadata often carries volatile fields (``updatedAtUtc``, fetch
    timestamps). Hashing it would mark every document changed on every run and
    re-embed the whole corpus.
    """
    digest = hashlib.sha256()
    for value in (
        document.id,
        document.title,
        document.locator,
        document.content_type,
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    digest.update(document.data)
    return digest.hexdigest()


def _artifact(document: SourceDocument, *, source_name: str) -> Artifact:
    metadata = dict(document.metadata)
    metadata.setdefault("documentId", document.id)
    metadata.setdefault("source", source_name)
    if document.title:
        metadata.setdefault("title", document.title)
    if document.uri:
        metadata.setdefault("sourceUrl", document.uri)
    return Artifact(
        step_id=document.id,
        uri=document.locator,
        content_type=document.content_type,
        data=document.data,
        metadata=metadata,
    )


__all__ = ["DocumentProvider", "ProviderSource", "SourceDocument"]
