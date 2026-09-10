"""Notion source — fetches pages shared with an integration via Notion's REST API."""

from __future__ import annotations

from typing import Any

from alvis._concurrency import gather_bounded
from alvis.core.models import Artifact, DocumentMeta
from alvis.sources.http import HttpClient

_API_VERSION = "2022-06-28"
_PAGE_SIZE = 100

_BLOCK_PREFIX: dict[str, str] = {
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "numbered_list_item": "1. ",
    "to_do": "- [ ] ",
    "quote": "> ",
}
"""Markdown prefix per Notion block ``type`` — everything not listed here
(``paragraph``, callouts, ...) renders as plain text, no prefix."""

_SKIPPED_BLOCK_TYPES = {"child_page", "child_database"}
"""Nested pages/databases are separate documents, not inline content —
skipped rather than inlined (and never recursed into)."""


def _rich_text(value: dict[str, Any]) -> str:
    return "".join(str(fragment.get("plain_text", "")) for fragment in value.get("rich_text", []))


def _render_block(block_type: str, value: dict[str, Any]) -> str:
    if block_type == "divider":
        return "---"
    if block_type == "code":
        language = value.get("language", "")
        return f"```{language}\n{_rich_text(value)}\n```"
    return _BLOCK_PREFIX.get(block_type, "") + _rich_text(value)


def _page_title(page: dict[str, Any]) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            text = "".join(str(f.get("plain_text", "")) for f in prop.get("title", []))
            return text or "Untitled"
    return "Untitled"


class NotionSource:
    """Yields one artifact per page the integration has been shared with.

    Config keys: ``api_token_env`` (a Notion integration token),
    ``max_concurrency`` (default 8 — page-content renders in flight at
    once).

    Uses Notion's REST API: ``/search`` lists every page the integration
    can see (Notion's own sharing model decides that — this source applies
    no filter of its own), ``/blocks/{id}/children`` (paginated, recursed
    into nested blocks) renders each page's content. Block content is
    rendered to a lightweight Markdown approximation — headings, lists,
    quotes, code, paragraphs — not a faithful reproduction of Notion's
    richer block types (tables, embeds, synced blocks, databases as
    tables), which fall back to their plain text if any, or are dropped.
    """

    def __init__(
        self,
        api_token_env: str,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        max_concurrency: int = 8,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.max_concurrency = max_concurrency
        self.client = HttpClient(
            base_url="https://api.notion.com/v1",
            api_token_env=api_token_env,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
            header_hook=lambda method, path, query: {"Notion-Version": _API_VERSION},
        )

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self.client.aclose()

    async def list_documents(self) -> list[DocumentMeta]:
        """Fingerprint pages by their last-edited timestamp (no content fetch)."""
        pages = await self._list_pages()
        return [
            DocumentMeta(
                uri=page["url"],
                step_id=page["id"],
                fingerprint=str(page.get("last_edited_time", "")),
                content_type="text/markdown",
            )
            for page in pages
        ]

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        """Render the wanted pages to Markdown artifacts.

        With ``uris``, only the given page URLs are rendered. Renders run
        concurrently, bounded by ``max_concurrency``.
        """
        pages = await self._list_pages()
        wanted = [page for page in pages if uris is None or page["url"] in uris]

        async def _fetch_one(page: dict[str, Any]) -> Artifact:
            page_id = page["id"]
            lines = await self._render_blocks(page_id)
            text = "\n\n".join(line for line in lines if line)
            return Artifact(
                step_id=page_id,
                uri=page["url"],
                content_type="text/markdown",
                data=text.encode("utf-8"),
                metadata={
                    "title": _page_title(page),
                    "documentId": page_id,
                    "lastEditedTime": str(page.get("last_edited_time", "")),
                },
            )

        return await gather_bounded(
            wanted, _fetch_one, max_concurrency=self.max_concurrency
        )

    async def _list_pages(self) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            payload: dict[str, Any] = {
                "filter": {"value": "page", "property": "object"},
                "page_size": _PAGE_SIZE,
            }
            if cursor:
                payload["start_cursor"] = cursor
            response = await self.client.request("POST", "/search", payload=payload)
            pages.extend(response.get("results", []))
            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")
            if not cursor:
                break
        return pages

    async def _render_blocks(self, block_id: str) -> list[str]:
        lines: list[str] = []
        for block in await self._list_block_children(block_id):
            block_type = str(block.get("type", ""))
            if block_type in _SKIPPED_BLOCK_TYPES:
                continue
            value = block.get(block_type, {})
            lines.append(_render_block(block_type, value))
            if block.get("has_children"):
                lines.extend(await self._render_blocks(block["id"]))
        return lines

    async def _list_block_children(self, block_id: str) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            query: dict[str, Any] = {"page_size": _PAGE_SIZE}
            if cursor:
                query["start_cursor"] = cursor
            response = await self.client.request(
                "GET", f"/blocks/{block_id}/children", query=query
            )
            blocks.extend(response.get("results", []))
            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")
            if not cursor:
                break
        return blocks


__all__ = ["NotionSource"]
