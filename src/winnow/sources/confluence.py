"""Confluence source — fetches pages of a space via the Confluence REST API."""

from __future__ import annotations

from winnow.core.models import Artifact
from winnow.sources.http import HttpClient


class ConfluenceSource:
    """Yields one artifact per page of the configured space.

    Config keys: ``url`` (base, e.g. https://wiki.example.com),
    ``space`` (space key), ``api_token_env`` (env var holding API token),
    ``username`` (optional, required for basic auth).

    Uses the classic ``/rest/api`` endpoints for broadest compatibility.
    """

    def __init__(
        self,
        url: str,
        space: str,
        api_token_env: str | None = None,
        username: str | None = None,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
    ) -> None:
        self.space = space
        self.client = HttpClient(
            base_url=url,
            api_token_env=api_token_env,
            username=username,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
        )

    async def fetch(self) -> list[Artifact]:
        """Fetch all pages of the configured space as HTML artifacts."""
        pages = await self.client.request(
            "GET",
            "/rest/api/content",
            query={
                "spaceKey": self.space,
                "type": "page",
                "limit": 500,
            },
        )
        artifacts: list[Artifact] = []
        for page in pages.get("results", []):
            page_id = page["id"]
            expanded = await self.client.request(
                "GET",
                f"/rest/api/content/{page_id}",
                query={"expand": "body.storage"},
            )
            body_html = (
                expanded.get("body", {}).get("storage", {}).get("value", "") or ""
            )
            artifacts.append(
                Artifact(
                    step_id=page_id,
                    uri=page["_links"]["webui"],
                    content_type="text/html",
                    data=body_html.encode("utf-8"),
                    metadata={
                        "title": page["title"],
                        "id": page_id,
                        "version": page.get("version", {}).get("number"),
                    },
                )
            )
        return artifacts