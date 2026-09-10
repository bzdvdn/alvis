"""Jira source — fetches issues of a project via the Jira Cloud REST API."""

from __future__ import annotations

from typing import Any

from alvis._concurrency import gather_bounded
from alvis.core.models import Artifact, DocumentMeta
from alvis.sources.http import HttpClient

_SEARCH_PAGE_SIZE = 100


class JiraSource:
    """Yields one artifact per issue matched by a project or JQL query.

    Config keys: ``url`` (base, e.g. https://acme.atlassian.net),
    ``project`` (project key, e.g. "ENG" — matched via ``project = "ENG"
    ORDER BY updated DESC``) or ``jql`` (a raw JQL query, for anything
    ``project`` can't express — takes precedence if both are given),
    ``api_token_env`` (env var holding the API token), ``username``
    (account email — Jira Cloud, like Confluence Cloud, uses Basic auth
    with email + API token).

    Uses the classic ``/rest/api/2`` endpoints, so ``description`` comes
    back as Jira's own wiki markup (a string) rather than API v3's
    Atlassian Document Format (structured JSON) — ingested as plain text,
    not rendered.
    """

    def __init__(
        self,
        url: str,
        project: str | None = None,
        jql: str | None = None,
        api_token_env: str | None = None,
        username: str | None = None,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        max_concurrency: int = 8,
    ) -> None:
        if not project and not jql:
            raise ValueError("jira source requires 'project' or 'jql'")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.max_concurrency = max_concurrency
        self.jql = jql or f'project = "{project}" ORDER BY updated DESC'
        self.web_base = url.rstrip("/")
        self.client = HttpClient(
            base_url=self.web_base,
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
        """Fingerprint issues by their last-updated timestamp (summary only)."""
        issues = await self._search(fields=["updated"])
        return [
            DocumentMeta(
                uri=self._issue_url(issue),
                step_id=issue["id"],
                fingerprint=str(issue.get("fields", {}).get("updated", "")),
                content_type="text/plain",
            )
            for issue in issues
        ]

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        """Fetch the wanted issues' summary + description as artifacts.

        With ``uris``, only the given issue URLs are expanded. Expansions
        run concurrently, bounded by ``max_concurrency``.
        """
        issues = await self._search(fields=["updated"])
        wanted = [
            issue for issue in issues if uris is None or self._issue_url(issue) in uris
        ]

        async def _fetch_one(issue: dict[str, Any]) -> Artifact:
            key = issue["key"]
            detail = await self.client.request(
                "GET",
                f"/rest/api/2/issue/{key}",
                query={"fields": "summary,description,status,issuetype,updated"},
            )
            fields = detail.get("fields", {})
            summary = str(fields.get("summary", ""))
            description = str(fields.get("description") or "")
            text = f"{summary}\n\n{description}".strip()
            return Artifact(
                step_id=detail["id"],
                uri=self._issue_url(detail),
                content_type="text/plain",
                data=text.encode("utf-8"),
                metadata={
                    "title": summary,
                    "documentId": key,
                    "status": (fields.get("status") or {}).get("name", ""),
                    "issueType": (fields.get("issuetype") or {}).get("name", ""),
                },
            )

        return await gather_bounded(
            wanted, _fetch_one, max_concurrency=self.max_concurrency
        )

    async def _search(self, *, fields: list[str]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        start_at = 0
        while True:
            response = await self.client.request(
                "GET",
                "/rest/api/2/search",
                query={
                    "jql": self.jql,
                    "fields": ",".join(fields),
                    "startAt": start_at,
                    "maxResults": _SEARCH_PAGE_SIZE,
                },
            )
            page = response.get("issues", [])
            issues.extend(page)
            total = int(response.get("total", len(issues)))
            start_at += len(page)
            if not page or start_at >= total:
                break
        return issues

    def _issue_url(self, issue: dict[str, Any]) -> str:
        return f"{self.web_base}/browse/{issue['key']}"


__all__ = ["JiraSource"]
