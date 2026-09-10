"""SharePointSource — REST client against a mocked Azure AD + Graph API."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from alvis.sources.base import SourceError
from alvis.sources.sharepoint import SharePointSource


def _source(*, token_handler=None, graph_handler=None) -> SharePointSource:  # noqa: ANN001
    source = SharePointSource(
        tenant_id="tenant-1",
        client_id="client-1",
        site_url="https://contoso.sharepoint.com/sites/TeamSite",
        client_secret_env="SP_SECRET",
    )
    if token_handler is not None:
        source._token_client.transport = httpx.MockTransport(token_handler)
    source.client.transport = httpx.MockTransport(graph_handler)
    return source


async def _token_ok(request: httpx.Request) -> httpx.Response:
    body = parse_qs(request.content.decode())
    assert body["grant_type"] == ["client_credentials"]
    assert body["client_secret"] == ["shh"]
    assert body["scope"] == ["https://graph.microsoft.com/.default"]
    return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})


def _graph_router(*, delta_items: list[dict], details: dict[str, dict] | None = None):  # noqa: ANN201
    details = details or {}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok-1"
        path = request.url.path
        if path == "/v1.0/sites/contoso.sharepoint.com:/sites/TeamSite":
            return httpx.Response(200, json={"id": "site-1"})
        if path == "/v1.0/sites/site-1/drive":
            return httpx.Response(200, json={"id": "drive-1"})
        if path == "/v1.0/drives/drive-1/root/delta":
            return httpx.Response(200, json={"value": delta_items})
        if path.startswith("/v1.0/drives/drive-1/items/"):
            item_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=details[item_id])
        raise AssertionError(f"unexpected request to {path}")

    return handler


def _file_item(item_id: str, name: str, **extra) -> dict:  # noqa: ANN003
    return {
        "id": item_id,
        "name": name,
        "webUrl": f"https://contoso.sharepoint.com/sites/TeamSite/{name}",
        "file": {"mimeType": "text/plain"},
        "eTag": f"etag-{item_id}",
        "lastModifiedDateTime": "2026-01-01T00:00:00Z",
        "parentReference": {"path": "/drive/root:/Docs"},
        **extra,
    }


async def test_missing_client_secret_raises() -> None:
    source = SharePointSource(
        tenant_id="t",
        client_id="c",
        site_url="https://contoso.sharepoint.com/sites/TeamSite",
        client_secret_env="MISSING_SP_SECRET_XYZ",
    )
    with pytest.raises(SourceError, match="MISSING_SP_SECRET_XYZ"):
        await source.list_documents()


async def test_token_request_includes_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_SECRET", "shh")
    captured: dict[str, list[str]] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})

    items = [_file_item("1", "a.md")]
    source = _source(token_handler=handler, graph_handler=_graph_router(delta_items=items))

    await source.list_documents()

    assert captured["client_id"] == ["client-1"]


async def test_list_documents_fingerprints_by_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_SECRET", "shh")
    items = [_file_item("1", "readme.md"), _file_item("2", "notes.txt")]
    source = _source(token_handler=_token_ok, graph_handler=_graph_router(delta_items=items))

    metas = await source.list_documents()

    assert [m.uri for m in metas] == [
        "https://contoso.sharepoint.com/sites/TeamSite/readme.md",
        "https://contoso.sharepoint.com/sites/TeamSite/notes.txt",
    ]
    assert metas[0].fingerprint == "etag-1"
    assert metas[0].content_type == "text/markdown"


async def test_list_documents_skips_folders_and_deleted_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SP_SECRET", "shh")
    items = [
        {"id": "f1", "name": "Docs", "folder": {}},
        {**_file_item("2", "gone.md"), "deleted": {"state": "deleted"}},
        _file_item("3", "keep.md"),
    ]
    source = _source(token_handler=_token_ok, graph_handler=_graph_router(delta_items=items))

    metas = await source.list_documents()

    assert [m.step_id for m in metas] == ["3"]


async def test_list_documents_respects_exclude_globs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_SECRET", "shh")
    items = [
        _file_item("1", "video.mp4", parentReference={"path": "/drive/root:/Media"}),
        _file_item("2", "readme.md"),
    ]
    source = SharePointSource(
        tenant_id="t",
        client_id="c",
        site_url="https://contoso.sharepoint.com/sites/TeamSite",
        client_secret_env="SP_SECRET",
        exclude_globs=["Media/*"],
    )
    source._token_client.transport = httpx.MockTransport(_token_ok)
    source.client.transport = httpx.MockTransport(_graph_router(delta_items=items))

    metas = await source.list_documents()

    assert [m.step_id for m in metas] == ["2"]


async def test_fetch_downloads_via_graph_download_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_SECRET", "shh")
    items = [_file_item("1", "readme.md")]
    details = {
        "1": {
            "id": "1",
            "name": "readme.md",
            "webUrl": "https://contoso.sharepoint.com/sites/TeamSite/readme.md",
            "@microsoft.graph.downloadUrl": "https://download.example.com/readme.md",
        }
    }
    source = _source(
        token_handler=_token_ok,
        graph_handler=_graph_router(delta_items=items, details=details),
    )

    async def download_handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"# Hello\n")

    source._download_client = httpx.AsyncClient(transport=httpx.MockTransport(download_handler))

    artifacts = await source.fetch()

    assert len(artifacts) == 1
    assert artifacts[0].data == b"# Hello\n"
    assert artifacts[0].uri == "https://contoso.sharepoint.com/sites/TeamSite/readme.md"
    assert artifacts[0].metadata["documentId"] == "1"


async def test_fetch_skips_items_without_download_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_SECRET", "shh")
    items = [_file_item("1", "readme.md")]
    details = {"1": {"id": "1", "name": "readme.md", "webUrl": "u/1"}}
    source = _source(
        token_handler=_token_ok,
        graph_handler=_graph_router(delta_items=items, details=details),
    )

    artifacts = await source.fetch()

    assert artifacts == []


async def test_fetch_filters_by_uris(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_SECRET", "shh")
    items = [_file_item("1", "a.md"), _file_item("2", "b.md")]
    details = {
        "1": {"id": "1", "name": "a.md", "webUrl": items[0]["webUrl"]},
        "2": {
            "id": "2",
            "name": "b.md",
            "webUrl": items[1]["webUrl"],
            "@microsoft.graph.downloadUrl": "https://download.example.com/b.md",
        },
    }
    source = _source(
        token_handler=_token_ok,
        graph_handler=_graph_router(delta_items=items, details=details),
    )

    async def download_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"body")

    source._download_client = httpx.AsyncClient(transport=httpx.MockTransport(download_handler))

    artifacts = await source.fetch(uris={items[1]["webUrl"]})

    assert [a.uri for a in artifacts] == [items[1]["webUrl"]]


async def test_fetch_enforces_max_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_SECRET", "shh")
    items = [_file_item("1", "big.md")]
    details = {
        "1": {
            "id": "1",
            "name": "big.md",
            "webUrl": items[0]["webUrl"],
            "@microsoft.graph.downloadUrl": "https://download.example.com/big.md",
        }
    }
    source = SharePointSource(
        tenant_id="t",
        client_id="c",
        site_url="https://contoso.sharepoint.com/sites/TeamSite",
        client_secret_env="SP_SECRET",
        max_bytes=4,
    )
    source._token_client.transport = httpx.MockTransport(_token_ok)
    source.client.transport = httpx.MockTransport(
        _graph_router(delta_items=items, details=details)
    )

    async def download_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"way too many bytes")

    source._download_client = httpx.AsyncClient(transport=httpx.MockTransport(download_handler))

    assert await source.fetch() == []


async def test_token_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_SECRET", "shh")
    token_calls = {"n": 0}

    async def token_handler(request: httpx.Request) -> httpx.Response:
        token_calls["n"] += 1
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})

    items = [_file_item("1", "a.md")]
    source = _source(
        token_handler=token_handler, graph_handler=_graph_router(delta_items=items)
    )

    await source.list_documents()
    await source.list_documents()

    assert token_calls["n"] == 1


def test_rejects_max_concurrency_below_one() -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        SharePointSource(
            tenant_id="t",
            client_id="c",
            site_url="https://contoso.sharepoint.com/sites/TeamSite",
            client_secret_env="SP_SECRET",
            max_concurrency=0,
        )


async def test_aclose_releases_all_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_SECRET", "shh")
    items = [_file_item("1", "a.md")]
    source = _source(token_handler=_token_ok, graph_handler=_graph_router(delta_items=items))
    source._download_client = httpx.AsyncClient()

    await source.list_documents()
    await source.aclose()

    assert source._download_client is None
