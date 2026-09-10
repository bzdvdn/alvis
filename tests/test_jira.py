"""JiraSource — REST client against a mocked Jira Cloud API."""

from __future__ import annotations

import httpx
import pytest

from alvis.sources.jira import JiraSource


def _source(handler) -> JiraSource:  # noqa: ANN001
    source = JiraSource(url="https://acme.atlassian.net", project="ENG")
    source.client.transport = httpx.MockTransport(handler)
    return source


def _issue(key: str, issue_id: str, updated: str = "2026-01-01T00:00:00.000+0000") -> dict:
    return {
        "id": issue_id,
        "key": key,
        "fields": {"updated": updated},
    }


async def test_requires_project_or_jql() -> None:
    with pytest.raises(ValueError, match="'project' or 'jql'"):
        JiraSource(url="https://acme.atlassian.net")


async def test_builds_jql_from_project() -> None:
    source = JiraSource(url="https://acme.atlassian.net", project="ENG")
    assert source.jql == 'project = "ENG" ORDER BY updated DESC'


async def test_raw_jql_takes_precedence_over_project() -> None:
    source = JiraSource(
        url="https://acme.atlassian.net", project="ENG", jql="assignee = currentUser()"
    )
    assert source.jql == "assignee = currentUser()"


async def test_list_documents_fingerprints_by_updated_timestamp() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/2/search"
        assert request.url.params["jql"] == 'project = "ENG" ORDER BY updated DESC'
        return httpx.Response(
            200,
            json={
                "issues": [
                    _issue("ENG-1", "1"),
                    _issue("ENG-2", "2", "2026-01-02T00:00:00.000+0000"),
                ],
                "total": 2,
            },
        )

    metas = await _source(handler).list_documents()

    assert [m.uri for m in metas] == [
        "https://acme.atlassian.net/browse/ENG-1",
        "https://acme.atlassian.net/browse/ENG-2",
    ]
    assert metas[0].fingerprint == "2026-01-01T00:00:00.000+0000"
    assert metas[0].content_type == "text/plain"


async def test_list_documents_paginates_search() -> None:
    calls: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        start_at = int(request.url.params["startAt"])
        calls.append(start_at)
        if start_at == 0:
            return httpx.Response(
                200, json={"issues": [_issue("ENG-1", "1")], "total": 2}
            )
        return httpx.Response(
            200, json={"issues": [_issue("ENG-2", "2")], "total": 2}
        )

    metas = await _source(handler).list_documents()

    assert calls == [0, 1]
    assert len(metas) == 2


async def test_fetch_builds_artifact_from_summary_and_description() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/2/search":
            return httpx.Response(200, json={"issues": [_issue("ENG-1", "1")], "total": 1})
        assert request.url.path == "/rest/api/2/issue/ENG-1"
        return httpx.Response(
            200,
            json={
                "id": "1",
                "key": "ENG-1",
                "fields": {
                    "summary": "Fix the retry bug",
                    "description": "Retries don't back off correctly.",
                    "status": {"name": "In Progress"},
                    "issuetype": {"name": "Bug"},
                    "updated": "2026-01-01T00:00:00.000+0000",
                },
            },
        )

    artifacts = await _source(handler).fetch()

    assert len(artifacts) == 1
    artifact = artifacts[0]
    text = artifact.data.decode("utf-8")
    assert "Fix the retry bug" in text
    assert "Retries don't back off correctly." in text
    assert artifact.uri == "https://acme.atlassian.net/browse/ENG-1"
    assert artifact.content_type == "text/plain"
    assert artifact.metadata["title"] == "Fix the retry bug"
    assert artifact.metadata["documentId"] == "ENG-1"
    assert artifact.metadata["status"] == "In Progress"
    assert artifact.metadata["issueType"] == "Bug"


async def test_fetch_handles_missing_description() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/2/search":
            return httpx.Response(200, json={"issues": [_issue("ENG-1", "1")], "total": 1})
        return httpx.Response(
            200,
            json={
                "id": "1",
                "key": "ENG-1",
                "fields": {"summary": "No description here", "description": None},
            },
        )

    artifacts = await _source(handler).fetch()

    assert artifacts[0].data.decode("utf-8") == "No description here"


async def test_fetch_filters_by_uris() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/2/search":
            return httpx.Response(
                200,
                json={
                    "issues": [_issue("ENG-1", "1"), _issue("ENG-2", "2")],
                    "total": 2,
                },
            )
        key = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200, json={"id": key, "key": key, "fields": {"summary": key}}
        )

    artifacts = await _source(handler).fetch(
        uris={"https://acme.atlassian.net/browse/ENG-2"}
    )

    assert [a.uri for a in artifacts] == ["https://acme.atlassian.net/browse/ENG-2"]


def test_rejects_max_concurrency_below_one() -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        JiraSource(url="https://acme.atlassian.net", project="ENG", max_concurrency=0)
