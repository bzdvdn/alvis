"""GitLab source — ingests text files from a project repository tree."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import quote

import httpx

from winnow.core.models import Artifact
from winnow.sources.base import SourceError
from winnow.sources.content_types import content_type, is_ingestible, matches_globs
from winnow.sources.http import HttpClient

_DEFAULT_API = "https://gitlab.com/api/v4"


class GitLabSource:
    """Yields an artifact per text blob in the project repository.

    Config keys: ``url`` (base host, default https://gitlab.com; works
    with self-hosted GitLab), ``project`` ("group/project"), ``branch``
    (default "main"), ``path`` (optional subtree filter),
    ``include_globs`` (only matching blobs are ingested),
    ``exclude_globs`` (matching blobs are skipped, e.g. videos — wins
    over ``include_globs``), ``api_token_env``. The ``api/v4`` suffix
    is appended to ``url``. Uses the raw blob endpoint, so
    ``HttpClient`` serves binary responses here.
    """

    def __init__(
        self,
        project: str,
        branch: str = "main",
        path: str | None = None,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        api_token_env: str | None = None,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
        per_page: int = 100,
        url: str = "https://gitlab.com",
        max_bytes: int | None = None,
    ) -> None:
        if url.endswith("/api/v4"):
            url = url[: -len("/api/v4")]
        self.web_base = url.rstrip("/")
        self.api_base = f"{self.web_base}/api/v4"
        self.project = quote(project, safe="")  # group/project → group%2Fproject
        self.project_web = quote(project, safe="/")
        self.branch = branch
        self.path = path
        self.include_globs = include_globs
        self.exclude_globs = exclude_globs
        self.per_page = per_page
        self.client = HttpClient(
            base_url=self.api_base,
            api_token_env=api_token_env,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
            transport=transport,
            max_bytes=max_bytes,
        )

    async def fetch(self) -> list[Artifact]:
        artifacts: list[Artifact] = []
        for item in await self._iter_tree():
            file_path = item["path"]
            if not self._wanted(file_path):
                continue
            try:
                blob = await self.client.request(
                    "GET",
                    f"/projects/{self.project}/repository/blobs/{item['id']}/raw",
                    ok_status=(200,),
                    raw=True,
                )
            except SourceError as exc:
                if exc.status_code == 413:
                    continue  # oversized blob skipped, others abort the run
                raise
            artifacts.append(
                Artifact(
                    step_id=item["id"],
                    uri=f"{self.web_base}/{self.project_web}/-/blob/"
                    f"{quote(self.branch)}/{file_path}",
                    content_type=content_type(file_path),
                    data=blob,
                    metadata={
                        "path": file_path,
                        "title": file_path.rsplit("/", 1)[-1],
                    },
                )
            )
        return artifacts

    async def _iter_tree(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = await self.client.request(
                "GET",
                f"/projects/{self.project}/repository/tree",
                query={
                    "ref": self.branch,
                    "recursive": "true",
                    "per_page": str(self.per_page),
                    "page": str(page),
                },
            )
            items.extend(cast(list[dict[str, Any]], batch))
            if len(batch) < self.per_page:
                break
            page += 1
        return items

    def _wanted(self, file_path: str) -> bool:
        if self.path and not file_path.startswith(self.path):
            return False
        if matches_globs(self.exclude_globs, file_path):
            return False
        if self.include_globs is not None:
            return matches_globs(self.include_globs, file_path)
        return is_ingestible(file_path)
