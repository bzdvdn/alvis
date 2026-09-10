"""GitHub source — ingests text files from a repository tree."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

import httpx

from alvis._concurrency import gather_bounded
from alvis.core.models import Artifact, DocumentMeta
from alvis.sources.base import SourceError
from alvis.sources.content_types import content_type, is_ingestible, matches_globs
from alvis.sources.http import HttpClient

_GITHUB_API = "https://api.github.com"


class GitHubSource:
    """Yields an artifact per text blob in the repository.

    Config keys: ``repo`` ("owner/name"), ``branch`` (default "main"),
    ``path`` (optional subtree filter), ``include_globs`` (only
    matching blobs are ingested), ``exclude_globs`` (matching blobs are
    skipped, e.g. videos — wins over ``include_globs``),
    ``api_token_env``, ``max_concurrency`` (default 8 — blob downloads in
    flight at once; the shared ``HttpClient`` connection pool makes this
    safe, see :mod:`alvis._concurrency`).
    """

    def __init__(
        self,
        repo: str,
        branch: str = "main",
        path: str | None = None,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        api_token_env: str | None = None,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
        max_bytes: int | None = None,
        max_concurrency: int = 8,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.repo = repo
        self.branch = branch
        self.path = path
        self.include_globs = include_globs
        self.exclude_globs = exclude_globs
        self.max_concurrency = max_concurrency
        self.client = HttpClient(
            base_url=_GITHUB_API,
            api_token_env=api_token_env,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
            transport=transport,
            max_bytes=max_bytes,
        )

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self.client.aclose()

    async def list_documents(self) -> list[DocumentMeta]:
        """Fingerprint blobs from the tree listing (blob sha, no download)."""
        return [
            DocumentMeta(
                uri=_blob_uri(self.repo, self.branch, item["path"]),
                step_id=item["sha"],
                fingerprint=item["sha"],
                content_type=content_type(item["path"]),
            )
            for item in await self._tree_items()
        ]

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        """Fetch the repository's wanted text blobs as artifacts.

        With ``uris``, only the given blob URIs are downloaded. Blob
        downloads run concurrently, bounded by ``max_concurrency``.
        """
        wanted = [
            item
            for item in await self._tree_items()
            if uris is None or _blob_uri(self.repo, self.branch, item["path"]) in uris
        ]

        async def _fetch_one(item: dict[str, Any]) -> Artifact | None:
            file_path = item["path"]
            uri = _blob_uri(self.repo, self.branch, file_path)
            try:
                info = await self.client.request(
                    "GET",
                    f"/repos/{self.repo}/contents/{quote(file_path, safe='/')}",
                )
            except SourceError as exc:
                if exc.status_code == 413:
                    return None  # oversized blob skipped, others abort the run
                raise
            data = base64.b64decode(info.get("content") or "")
            return Artifact(
                step_id=item["sha"],
                uri=uri,
                content_type=content_type(file_path),
                data=data,
                metadata={
                    "path": file_path,
                    "title": file_path.rsplit("/", 1)[-1],
                    "html_url": info.get("html_url") or "",
                    "documentId": item["sha"],
                },
            )

        results = await gather_bounded(
            wanted, _fetch_one, max_concurrency=self.max_concurrency
        )
        return [artifact for artifact in results if artifact is not None]

    async def _tree_items(self) -> list[dict[str, Any]]:
        tree = await self.client.request(
            "GET",
            f"/repos/{self.repo}/git/trees/{quote(self.branch)}",
            query={"recursive": "1"},
        )
        items: list[dict[str, Any]] = []
        for item in tree.get("tree", []):
            if item.get("type") != "blob":
                continue
            file_path = str(item["path"])
            if not self._wanted(file_path):
                continue
            items.append(item)
        return items

    def _wanted(self, file_path: str) -> bool:
        if self.path and not file_path.startswith(self.path):
            return False
        if matches_globs(self.exclude_globs, file_path):
            return False
        if self.include_globs is not None:
            return matches_globs(self.include_globs, file_path)
        return is_ingestible(file_path)


def _blob_uri(repo: str, branch: str, file_path: str) -> str:
    return f"https://github.com/{repo}/blob/{quote(branch)}/{file_path}"