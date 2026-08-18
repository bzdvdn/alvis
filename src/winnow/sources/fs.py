"""Filesystem source — ingests local text documents into the pipeline."""

from __future__ import annotations

import asyncio
from pathlib import Path

from winnow.core.models import Artifact
from winnow.sources.base import SourceError
from winnow.sources.content_types import content_type as ct_from_path
from winnow.sources.content_types import is_ingestible


class FilesystemSource:
    """Yields one artifact per text file under ``path`` (recursively)."""

    def __init__(self, path: str, pattern: str | None = None) -> None:
        self.root = Path(path)
        self.pattern = pattern

    async def fetch(self) -> list[Artifact]:
        files = self._discover()
        artifacts: list[Artifact] = []
        for file_path in files:
            try:
                data = await asyncio.to_thread(file_path.read_bytes)
            except OSError as exc:
                raise SourceError(f"failed to read {file_path}: {exc}") from exc
            artifacts.append(
                Artifact(
                    step_id=str(file_path),
                    uri=str(file_path.resolve()),
                    content_type=ct_from_path(file_path),
                    data=data,
                    metadata={"path": str(file_path), "title": file_path.stem},
                )
            )
        return artifacts

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