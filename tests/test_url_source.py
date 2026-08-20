from __future__ import annotations

import hashlib

import httpx
import pytest

from alvis.sources.base import SourceError
from alvis.sources.url import StaticUrlSource

_HTML = b"<html><head><title>NikaRD docs</title></head><body><h1>Welcome</h1></body></html>"
_OTHER_HTML = b"<html><head><title>About</title></head><body><p>About us</p></body></html>"


def _transport(
    *,
    head: int | None = 200,
    get: int = 200,
    etag: str | None = "v1",
    last_modified: str | None = None,
) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        headers = {}
        if etag is not None:
            headers["ETag"] = etag
        if last_modified is not None:
            headers["Last-Modified"] = last_modified
        if request.method == "HEAD":
            assert head is not None
            return httpx.Response(head, content=b"", headers=headers)
        body = _OTHER_HTML if request.url.path == "/about" else _HTML
        return httpx.Response(get, content=body, headers=headers)

    return httpx.MockTransport(handler)


async def test_fetch_downloads_pages_and_sets_metadata() -> None:
    source = StaticUrlSource(
        urls=["https://docs.example.com/", "https://docs.example.com/about"],
        transport=_transport(),
    )
    artifacts = await source.fetch()
    assert [a.uri for a in artifacts] == [
        "https://docs.example.com/",
        "https://docs.example.com/about",
    ]
    assert artifacts[0].data == _HTML
    assert artifacts[0].content_type == "text/html"
    assert artifacts[0].metadata == {
        "path": "https://docs.example.com/",
        "sourceUrl": "https://docs.example.com/",
        "title": "NikaRD docs",
    }
    assert artifacts[1].metadata["title"] == "About"


async def test_fetch_respects_uris_subset() -> None:
    source = StaticUrlSource(
        urls=["https://docs.example.com/", "https://docs.example.com/about"],
        transport=_transport(),
    )
    artifacts = await source.fetch(uris={"https://docs.example.com/about"})
    assert [a.uri for a in artifacts] == ["https://docs.example.com/about"]


async def test_fetch_skips_oversized_pages() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100)

    source = StaticUrlSource(
        urls=["https://docs.example.com/"],
        max_bytes=50,
        transport=httpx.MockTransport(handler),
    )
    assert await source.fetch() == []


async def test_listing_fingerprints_from_etag_without_downloading() -> None:
    source = StaticUrlSource(
        urls=["https://docs.example.com/"],
        transport=_transport(),
    )
    metas = await source.list_documents()
    assert [m.uri for m in metas] == ["https://docs.example.com/"]
    assert metas[0].fingerprint == "v1"
    assert metas[0].content_type == "text/html"


async def test_listing_uses_last_modified_when_no_etag() -> None:
    source = StaticUrlSource(
        urls=["https://docs.example.com/"],
        transport=_transport(etag=None, last_modified="Wed, 21 Oct 2026 07:28:00 GMT"),
    )
    metas = await source.list_documents()
    assert metas[0].fingerprint == "Wed, 21 Oct 2026 07:28:00 GMT"


async def test_listing_falls_back_to_get_and_hash_without_validators() -> None:
    source = StaticUrlSource(
        urls=["https://docs.example.com/"],
        transport=_transport(etag=None),
    )
    metas = await source.list_documents()
    assert metas[0].fingerprint == hashlib.sha256(_HTML).hexdigest()


async def test_listing_get_fallback_when_head_rejected() -> None:
    source = StaticUrlSource(
        urls=["https://docs.example.com/"],
        transport=_transport(head=405),
    )
    metas = await source.list_documents()
    assert metas[0].fingerprint == hashlib.sha256(_HTML).hexdigest()


async def test_listing_skips_removed_pages() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"not found")

    source = StaticUrlSource(
        urls=["https://docs.example.com/old"],
        transport=httpx.MockTransport(handler),
    )
    assert await source.list_documents() == []


async def test_empty_urls_is_a_config_error() -> None:
    with pytest.raises(SourceError, match="urls"):
        StaticUrlSource(urls=[])


async def test_bearer_token_header_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200, content=_HTML)

    monkeypatch.setenv("URL_SOURCE_TOKEN", "sekrit")
    source = StaticUrlSource(
        urls=["https://docs.example.com/"],
        api_token_env="URL_SOURCE_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    await source.fetch()
    assert seen[0]["authorization"] == "Bearer sekrit"


async def test_missing_token_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("URL_SOURCE_TOKEN", raising=False)
    source = StaticUrlSource(
        urls=["https://docs.example.com/"],
        api_token_env="URL_SOURCE_TOKEN",
        transport=_transport(),
    )
    with pytest.raises(SourceError, match="environment variable"):
        await source.fetch()


async def test_retryable_status_is_retried_then_succeeds() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, content=b"busy")
        return httpx.Response(200, content=_HTML)

    source = StaticUrlSource(
        urls=["https://docs.example.com/"],
        retries=1,
        retry_backoff=0.0,
        transport=httpx.MockTransport(handler),
    )
    assert await source.fetch()
    assert attempts == 2


async def test_listing_fallback_skips_page_over_byte_cap() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 500)

    source = StaticUrlSource(
        urls=["https://docs.example.com/big"],
        max_bytes=100,
        transport=httpx.MockTransport(handler),
    )
    assert await source.list_documents() == []