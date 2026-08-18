"""GitHub source — ingests text files from a repository tree."""

from __future__ import annotations

import base64
from urllib.parse import quote

import httpx

from winnow.core.models import Artifact
from winnow.sources.content_types import content_type, is_text_file, matches_globs
from winnow.sources.http import HttpClient

_GITHUB_API = "https://api.github.com"


class GitHubSource:
    """Yields an artifact per text blob in the repository.

    Config keys: ``repo`` ("owner/name"), ``branch`` (default "main"),
    ``path`` (optional subtree filter), ``include_globs`` (only
    matching blobs are ingested), ``exclude_globs`` (matching blobs are
    skipped, e.g. videos — wins over ``include_globs``),
    ``api_token_env``.
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
    ) -> None:
        self.repo = repo
        self.branch = branch
        self.path = path
        self.include_globs = include_globs
        self.exclude_globs = exclude_globs
        self.client = HttpClient(
            base_url=_GITHUB_API,
            api_token_env=api_token_env,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
            transport=transport,
        )

    async def fetch(self) -> list[Artifact]:
        tree = await self.client.request(
            "GET",
            f"/repos/{self.repo}/git/trees/{quote(self.branch)}",
            query={"recursive": "1"},
        )
        artifacts: list[Artifact] = []
        for item in tree.get("tree", []):
            if item.get("type") != "blob":
                continue
            file_path = item["path"]
            if not self._wanted(file_path):
                continue
            info = await self.client.request(
                "GET",
                f"/repos/{self.repo}/contents/{quote(file_path, safe='/')}",
            )
            data = base64.b64decode(info.get("content") or "")
            artifacts.append(
                Artifact(
                    step_id=item["sha"],
                    uri=f"https://github.com/{self.repo}/blob/{quote(self.branch)}/{file_path}",
                    content_type=content_type(file_path),
                    data=data,
                    metadata={
                        "path": file_path,
                        "title": file_path.rsplit("/", 1)[-1],
                        "html_url": info.get("html_url") or "",
                    },
                )
            )
        return artifacts

    def _wanted(self, file_path: str) -> bool:
        if self.path and not file_path.startswith(self.path):
            return False
        if matches_globs(self.exclude_globs, file_path):
            return False
        if self.include_globs is not None:
            return matches_globs(self.include_globs, file_path)
        return is_text_file(file_path)