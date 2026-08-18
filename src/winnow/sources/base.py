"""Source adapter protocol."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from winnow.core.models import Artifact


class Source(Protocol):
    """Produces artifacts for the pipeline."""

    def fetch(self) -> Iterator[Artifact]:
        """Yield artifacts; may be lazy/streaming for large sources."""
        ...


class SourceError(Exception):
    """Raised when a source adapter fails during fetch."""