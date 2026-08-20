"""GitLab source — ingests text files from a project repository tree."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import quote

import httpx

from alvis.core.models import Artifact, DocumentMeta
from alvis.sources.base import SourceError
from alvis.sources.content_types import content_type, is_ingestible, matches_globs
from alvis.sources.http import HttpClient

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

    async def list_documents(self) -> list[DocumentMeta]:
        """Fingerprint blobs from the tree listing (blob sha, no download)."""
        return [
            DocumentMeta(
                uri=_blob_uri(self.web_base, self.project_web, self.branch, item["path"]),
                step_id=item["id"],
                fingerprint=item["id"],
                content_type=content_type(item["path"]),
            )
            for item in await self._tree_items()
        ]

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        """Fetch the project's wanted text blobs as artifacts.

        With ``uris``, only the given blob URIs are downloaded.
        """
        artifacts: list[Artifact] = []
        for item in await self._tree_items():
            file_path = item["path"]
            uri = _blob_uri(self.web_base, self.project_web, self.branch, file_path)
            if uris is not None and uri not in uris:
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
                    uri=uri,
                    content_type=content_type(file_path),
                    data=blob,
                    metadata={
                        "path": file_path,
                        "title": file_path.rsplit("/", 1)[-1],
                        "documentId": item["id"],
                    },
                )
            )
        return artifacts

    async def _tree_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for item in await self._iter_tree():
            file_path = str(item["path"])
            if not self._wanted(file_path):
                continue
            items.append(item)
        return items

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


def _blob_uri(web_base: str, project_web: str, branch: str, file_path: str) -> str:
    return f"{web_base}/{project_web}/-/blob/{quote(branch)}/{file_path}"
