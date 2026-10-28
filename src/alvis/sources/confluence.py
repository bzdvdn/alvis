"""Confluence source — fetches pages of a space or a page tree via the REST API."""

from __future__ import annotations

from typing import Any

from alvis._concurrency import gather_bounded
from alvis.core.models import Artifact, DocumentMeta
from alvis.sources.http import HttpClient

_PAGE_LIMIT = 100


class ConfluenceSource:
    """Yields one artifact per page of the configured space or page tree.

    Config keys: ``url`` (base, e.g. https://wiki.example.com),
    ``space`` (space key), ``page_id`` (root of a page tree), ``api_token_env``
    (env var holding API token), ``username`` (optional, required for basic
    auth).

    ``space`` ingests every page in the space; ``page_id`` ingests that page
    and all of its descendants (recursively), which is useful when only one
    documentation/support tree should be indexed. At least one of the two is
    required; when both are set, ``page_id`` scopes the ingestion to its tree.

    Uses the classic ``/rest/api`` endpoints for broadest compatibility.
    """

    def __init__(
        self,
        url: str,
        space: str | None = None,
        page_id: str | int | None = None,
        api_token_env: str | None = None,
        username: str | None = None,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        max_concurrency: int = 8,
    ) -> None:
        if not space and page_id is None:
            raise ValueError("confluence source requires 'space' or 'page_id'")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.max_concurrency = max_concurrency
        self.space = space
        self.page_id = str(page_id) if page_id is not None else None
        self.client = HttpClient(
            base_url=url,
            api_token_env=api_token_env,
            username=username,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
        )

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self.client.aclose()

    async def list_documents(self) -> list[DocumentMeta]:
        """Fingerprint pages by their version number (no expanded bodies)."""
        pages = await self._list_pages()
        return [
            DocumentMeta(
                uri=_page_uri(page),
                step_id=page["id"],
                fingerprint=f"v{page.get('version', {}).get('number', 0)}",
                content_type="text/html",
            )
            for page in pages
        ]

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        """Fetch pages (expanded HTML bodies) as artifacts.

        With ``uris``, only the given page webui URIs are expanded. Page
        expansions run concurrently, bounded by ``max_concurrency``.
        """
        pages = await self._list_pages()
        wanted = [
            page for page in pages if uris is None or _page_uri(page) in uris
        ]

        async def _fetch_one(page: dict[str, Any]) -> Artifact:
            page_id = page["id"]
            uri = _page_uri(page)
            expanded = await self.client.request(
                "GET",
                f"/rest/api/content/{page_id}",
                query={"expand": "body.storage"},
            )
            body_html = (
                expanded.get("body", {}).get("storage", {}).get("value", "") or ""
            )
            return Artifact(
                step_id=page_id,
                uri=uri,
                content_type="text/html",
                data=body_html.encode("utf-8"),
                metadata={
                    "title": page["title"],
                    "id": page_id,
                    "documentId": page_id,
                    "version": page.get("version", {}).get("number"),
                },
            )

        return await gather_bounded(
            wanted, _fetch_one, max_concurrency=self.max_concurrency
        )

    async def _list_pages(self) -> list[dict[str, Any]]:
        if self.page_id is not None:
            return await self._list_tree(self.page_id)
        return await self._list_space_pages()

    async def _list_space_pages(self) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        start = 0
        while True:
            batch = await self.client.request(
                "GET",
                "/rest/api/content",
                query={
                    "spaceKey": self.space,
                    "type": "page",
                    "status": "current",
                    "limit": _PAGE_LIMIT,
                    "start": start,
                },
            )
            results = list(batch.get("results", []))
            pages.extend(results)
            if len(results) < _PAGE_LIMIT:
                break
            start += len(results)
        return pages

    async def _list_tree(self, root_id: str) -> list[dict[str, Any]]:
        """The root page followed by every descendant, breadth-first."""
        root = await self.client.request(
            "GET",
            f"/rest/api/content/{root_id}",
            query={"expand": "version"},
        )
        pages = [root]
        seen = {root_id}
        queue = [root_id]
        while queue:
            parent_id = queue.pop(0)
            for child in await self._list_children(parent_id):
                child_id = str(child.get("id") or "")
                if not child_id or child_id in seen:
                    continue
                seen.add(child_id)
                pages.append(child)
                queue.append(child_id)
        return pages

    async def _list_children(self, page_id: str) -> list[dict[str, Any]]:
        children: list[dict[str, Any]] = []
        start = 0
        while True:
            batch = await self.client.request(
                "GET",
                f"/rest/api/content/{page_id}/child/page",
                query={
                    "limit": _PAGE_LIMIT,
                    "start": start,
                    "expand": "version",
                },
            )
            results = list(batch.get("results", []))
            children.extend(results)
            if len(results) < _PAGE_LIMIT:
                break
            start += len(results)
        return children


def _page_uri(page: dict[str, Any]) -> str:
    """The page's web UI URI, with a pageId fallback for sparse payloads."""
    links = page.get("_links") or {}
    webui = links.get("webui")
    if webui:
        return str(webui)
    return f"/pages/viewpage.action?pageId={page['id']}"


__all__ = ["ConfluenceSource"]
