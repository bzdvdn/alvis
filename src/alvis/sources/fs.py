"""Filesystem source — ingests local text documents into the pipeline."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from alvis.core.models import Artifact, DocumentMeta
from alvis.sources.base import SourceError
from alvis.sources.content_types import content_type as ct_from_path
from alvis.sources.content_types import is_ingestible


class FilesystemSource:
    """Yields one artifact per text file under ``path`` (recursively)."""

    def __init__(
        self,
        path: str,
        pattern: str | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self.root = Path(path)
        self.pattern = pattern
        self.max_bytes = max_bytes

    async def list_documents(self) -> list[DocumentMeta]:
        """Fingerprint every file by its content hash (local reads are cheap)."""
        metas: list[DocumentMeta] = []
        for file_path in self._discover():
            if self.max_bytes is not None:
                try:
                    if file_path.stat().st_size > self.max_bytes:
                        continue
                except OSError:
                    continue
            data = await self._read(file_path)
            metas.append(
                DocumentMeta(
                    uri=str(file_path.resolve()),
                    step_id=str(file_path),
                    fingerprint=hashlib.sha256(data).hexdigest(),
                    content_type=ct_from_path(file_path),
                )
            )
        return metas

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        """Read ingestible text files under ``path`` into artifacts.

        With ``uris``, only the given absolute file paths are read.
        """
        wanted = {str(Path(uri).resolve()) for uri in uris} if uris is not None else None
        artifacts: list[Artifact] = []
        for file_path in self._discover():
            uri = str(file_path.resolve())
            if wanted is not None and uri not in wanted:
                continue
            if self.max_bytes is not None:
                try:
                    if file_path.stat().st_size > self.max_bytes:
                        continue
                except OSError:
                    continue
            data = await self._read(file_path)
            artifacts.append(
                Artifact(
                    step_id=str(file_path),
                    uri=uri,
                    content_type=ct_from_path(file_path),
                    data=data,
                    metadata={"path": str(file_path), "title": file_path.stem},
                )
            )
        return artifacts

    async def _read(self, file_path: Path) -> bytes:
        try:
            return await asyncio.to_thread(file_path.read_bytes)
        except OSError as exc:
            raise SourceError(f"failed to read {file_path}: {exc}") from exc

    def _discover(self) -> list[Path]:
        if not self.root.exists():
            raise SourceError(f"path does not exist: {self.root}")
        if self.root.is_file():
            return [self.root]
        pattern = self.pattern or "**/*"
        try:
            files = sorted(p for p in self.root.glob(pattern) if p.is_file())
        except OSError as exc:
            raise SourceError(f"failed to scan {self.root}: {exc}") from exc
        if self.pattern is None:
            files = [p for p in files if is_ingestible(p)]
        return files