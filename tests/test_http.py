from __future__ import annotations

import httpx
import pytest

from winnow.sources.base import SourceError
from winnow.sources.http import HttpClient


def _client(*, retries: int = 3, retry_backoff: float = 0.0, handler=None) -> HttpClient:
    client = HttpClient(
        base_url="http://localhost:6333",
        retries=retries,
        retry_backoff=retry_backoff,
        transport=httpx.MockTransport(handler),
    )
    return client


async def test_retries_then_succeeds() -> None:
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"ok": True})

    client = _client(handler=handler)
    result = await client.request("GET", "/x")
    assert result == {"ok": True}
    assert calls["n"] == 3


async def test_retries_exhausted_raises() -> None:
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(502, json={})

    client = _client(retries=2, handler=handler)
    with pytest.raises(SourceError, match="after 2 retries"):
        await client.request("GET", "/x")
    assert calls["n"] == 3


async def test_network_error_is_retried() -> None:
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={})

    client = _client(handler=handler)
    await client.request("GET", "/x")
    assert calls["n"] == 2


async def test_4xx_is_not_retried() -> None:
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={})

    client = _client(retries=3, handler=handler)
    with pytest.raises(SourceError):
        await client.request("GET", "/x")
    assert calls["n"] == 1


async def test_retry_after_header_is_respected() -> None:
    """429 with Retry-After: 0 keeps the retry delay at zero (fast in tests)."""
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"ok": True})

    client = _client(handler=handler)
    await client.request("GET", "/x")
    assert calls["n"] == 2


async def test_verify_passthrough_does_not_break_requests() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = HttpClient(
        base_url="https://wiki.example.com",
        verify=False,
        transport=httpx.MockTransport(handler),
    )
    assert await client.request("GET", "/rest/api") == {"ok": True}


async def test_factory_config_passes_retries_and_verify() -> None:
    from winnow.config.models import SourceConfig
    from winnow.factories import build_source

    source = build_source(
        SourceConfig(
            type="confluence",
            config={
                "url": "https://wiki.example.com",
                "space": "TEAM",
                "retries": 5,
                "retry_backoff": 2.5,
                "verify": False,
            },
        )
    )
    assert source.client.max_retries == 5
    assert source.client.retry_backoff == 2.5
    assert source.client.verify is False