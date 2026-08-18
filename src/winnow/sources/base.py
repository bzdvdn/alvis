"""Source adapter protocol."""

from __future__ import annotations

from typing import Protocol

from winnow.core.models import Artifact


class Source(Protocol):
    """Produces artifacts for the pipeline."""

    async def fetch(self) -> list[Artifact]:
        """Fetch all artifacts; may be overridden with streaming later."""
        ...


class SourceError(Exception):
    """Raised when a source adapter fails during fetch."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code