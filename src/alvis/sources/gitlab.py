"""GitLab source — ingests text files from project repository trees."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import quote

import httpx

from alvis._concurrency import gather_bounded
from alvis.core.models import Artifact, DocumentMeta
from alvis.sources.base import SourceError
from alvis.sources.content_types import content_type, is_ingestible, matches_globs
from alvis.sources.http import HttpClient

_DEFAULT_API = "https://gitlab.com/api/v4"


class GitLabSource:
    """Yields an artifact per text blob in one or more project repositories.

    Config keys: ``url`` (base host, default https://gitlab.com; works
    with self-hosted GitLab), ``project`` ("group/project"), ``group``
    ("group" — expands to every project in the group), ``branch``
    (default "main"), ``path`` (optional subtree filter),
    ``include_globs`` (only matching blobs are ingested),
    ``exclude_globs`` (matching blobs are skipped, e.g. videos — wins
    over ``include_globs``), ``project_include_globs`` / ``project_exclude_globs``
    (filter which group projects are traversed, matched against the full
    "group/project" path), ``include_archived`` (also traverse archived group
    projects; default false), ``api_token_env``. The ``api/v4`` suffix is
    appended to ``url``. Uses the raw blob endpoint, so ``HttpClient`` serves
    binary responses here.

    Either ``project`` or ``group`` must be supplied. When ``group`` is set the
    source lists the group's projects (paginated), applies the project globs,
    and traverses each surviving project's tree.
    """

    def __init__(
        self,
        project: str | None = None,
        group: str | None = None,
        branch: str = "main",
        path: str | None = None,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        project_include_globs: list[str] | None = None,
        project_exclude_globs: list[str] | None = None,
        include_archived: bool = False,
        api_token_env: str | None = None,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
        per_page: int = 100,
        url: str = "https://gitlab.com",
        max_bytes: int | None = None,
        max_concurrency: int = 8,
    ) -> None:
        if project is None and group is None:
            raise ValueError("gitlab source requires 'project' or 'group'")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.max_concurrency = max_concurrency
        if url.endswith("/api/v4"):
            url = url[: -len("/api/v4")]
        self.web_base = url.rstrip("/")
        self.api_base = f"{self.web_base}/api/v4"
        self.project = project
        self.group = group
        self.branch = branch
        self.path = path
        self.include_globs = include_globs
        self.exclude_globs = exclude_globs
        self.project_include_globs = project_include_globs
        self.project_exclude_globs = project_exclude_globs
        self.include_archived = include_archived
        self.per_page = per_page
        self._resolved_projects: list[str] | None = None
        self.client = HttpClient(
            base_url=self.api_base,
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

    async def _resolve_projects(self) -> list[str]:
        if self._resolved_projects is not None:
            return self._resolved_projects
        if self.group is None:
            projects = [cast(str, self.project)]
        else:
            projects = await self._group_projects()
        self._resolved_projects = projects
        return projects

    async def _group_projects(self) -> list[str]:
        group = quote(cast(str, self.group), safe="")  # group → group
        wanted: list[str] = []
        page = 1
        while True:
            batch = await self.client.request(
                "GET",
                f"/groups/{group}/projects",
                query={
                    "per_page": str(self.per_page),
                    "page": str(page),
                    "archived": "true" if self.include_archived else "false",
                    "with_shared": "false",
                },
            )
            items = cast(list[dict[str, Any]], batch)
            for proj in items:
                full_path = str(proj.get("path_with_namespace", ""))
                if not full_path:
                    continue
                if not self._project_wanted(full_path):
                    continue
                wanted.append(full_path)
            if len(items) < self.per_page:
                break
            page += 1
        return wanted

    def _project_wanted(self, full_path: str) -> bool:
        if matches_globs(self.project_exclude_globs, full_path):
            return False
        if self.project_include_globs is not None:
            return matches_globs(self.project_include_globs, full_path)
        return True

    async def list_documents(self) -> list[DocumentMeta]:
        """Fingerprint blobs from the tree listing (blob sha, no download)."""
        metas: list[DocumentMeta] = []
        for project in await self._resolve_projects():
            encoded = quote(project, safe="")
            web = quote(project, safe="/")
            for item in await self._tree_items(encoded):
                metas.append(
                    DocumentMeta(
                        uri=_blob_uri(self.web_base, web, self.branch, item["path"]),
                        step_id=item["id"],
                        fingerprint=item["id"],
                        content_type=content_type(item["path"]),
                    )
                )
        return metas

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        """Fetch the wanted text blobs across the resolved projects.

        With ``uris``, only the given blob URIs are downloaded. Blob
        downloads run concurrently across all wanted blobs (any project),
        bounded by ``max_concurrency``.
        """
        wanted: list[tuple[str, str, str, dict[str, Any]]] = []
        for project in await self._resolve_projects():
            encoded = quote(project, safe="")
            web = quote(project, safe="/")
            for item in await self._tree_items(encoded):
                file_path = item["path"]
                uri = _blob_uri(self.web_base, web, self.branch, file_path)
                if uris is not None and uri not in uris:
                    continue
                wanted.append((project, encoded, uri, item))

        async def _fetch_one(
            entry: tuple[str, str, str, dict[str, Any]],
        ) -> Artifact | None:
            project, encoded, uri, item = entry
            file_path = item["path"]
            try:
                blob = await self.client.request(
                    "GET",
                    f"/projects/{encoded}/repository/blobs/{item['id']}/raw",
                    ok_status=(200,),
                    raw=True,
                )
            except SourceError as exc:
                if exc.status_code == 413:
                    return None  # oversized blob skipped, others abort the run
                raise
            return Artifact(
                step_id=item["id"],
                uri=uri,
                content_type=content_type(file_path),
                data=blob,
                metadata={
                    "path": file_path,
                    "title": file_path.rsplit("/", 1)[-1],
                    "documentId": item["id"],
                    "project": project,
                },
            )

        results = await gather_bounded(
            wanted, _fetch_one, max_concurrency=self.max_concurrency
        )
        return [artifact for artifact in results if artifact is not None]

    async def _tree_items(self, project: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for item in await self._iter_tree(project):
            file_path = str(item["path"])
            if not self._wanted(file_path):
                continue
            items.append(item)
        return items

    async def _iter_tree(self, project: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = await self.client.request(
                "GET",
                f"/projects/{project}/repository/tree",
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
