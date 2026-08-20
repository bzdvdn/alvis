"""Source adapter protocol."""

from __future__ import annotations

from typing import Protocol

from alvis.core.models import Artifact, DocumentMeta


class Source(Protocol):
    """Produces artifacts for the pipeline."""

    async def fetch(self) -> list[Artifact]:
        """Fetch all artifacts; may be overridden with streaming later."""
        ...


class ListingSource(Protocol):
    """Optional cheap-listing capability for incremental ingestion.

    Connectors that can fingerprint documents from *listing data alone* —
    without downloading bodies (S3 ETag, GitLab/GitHub blob sha, Confluence
    version, local file hash) — implement :meth:`list_documents` and accept
    ``fetch(uris=...)``. The engine then skips fetching unchanged documents
    entirely, not just reprocessing them.
    """

    async def list_documents(self) -> list[DocumentMeta]:
        """Return one meta entry per document, fingerprint from the listing."""
        ...

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        """Fetch only the given ``uris`` (or all when ``uris`` is ``None``)."""
        ...


class SourceError(Exception):
    """Raised when a source adapter fails during fetch."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code