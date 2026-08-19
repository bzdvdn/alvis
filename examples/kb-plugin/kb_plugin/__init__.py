"""A "catalog" source — the worked example for the Plugin SDK (v1.1).

This is a standalone, installable extension package: it needs no changes to
Winnow's core. Installing it makes ``source.type: catalog`` available to any
pipeline, and ``winnow plugins`` lists it once discovered.

Run from this directory::

    pip install -e .
    winnow plugins                    # -> kb-catalog 0.1.0
    winnow validate catalog.yaml

The source ingests every ``.txt`` file under ``path``, tagging chunks with a
``catalog`` metadata key so downstream apps can filter on it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from winnow.core.models import Artifact, DocumentMeta
from winnow.plugin import Plugin
from winnow.sources.base import SourceError
from winnow.sources.content_types import content_type


class CatalogSource:
    """A tiny ListingSource over a directory of ``.txt`` files."""

    def __init__(self, path: str, tag: str = "default") -> None:
        self.root = Path(path)
        self.tag = tag

    async def list_documents(self) -> list[DocumentMeta]:
        return [
            DocumentMeta(
                uri=str(file_path.resolve()),
                step_id=str(file_path),
                fingerprint=hashlib.sha256(file_path.read_bytes()).hexdigest(),
                content_type=content_type(file_path),
            )
            for file_path in self._files()
        ]

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        wanted = {str(Path(uri).resolve()) for uri in uris} if uris is not None else None
        artifacts: list[Artifact] = []
        for file_path in self._files():
            uri = str(file_path.resolve())
            if wanted is not None and uri not in wanted:
                continue
            artifacts.append(
                Artifact(
                    step_id=str(file_path),
                    uri=uri,
                    content_type=content_type(file_path),
                    data=file_path.read_bytes(),
                    metadata={"path": str(file_path), "catalog": self.tag},
                )
            )
        return artifacts

    def _files(self) -> list[Path]:
        if not self.root.exists():
            raise SourceError(f"path does not exist: {self.root}")
        return sorted(p for p in self.root.glob("*.txt") if p.is_file())


def _build_catalog(*, config, max_bytes=None) -> CatalogSource:  # noqa: ARG001
    return CatalogSource(**config.config)


plugin = Plugin(
    name="kb-catalog",
    version="0.1.0",
    summary="Reads .txt files as catalog documents",
    sources={"catalog": _build_catalog},
)