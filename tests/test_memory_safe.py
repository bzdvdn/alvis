"""Memory-safe fetch: streaming byte caps and oversized-artifact skipping."""

from __future__ import annotations

import httpx
import pytest

from alvis.sources.base import SourceError
from alvis.sources.fs import FilesystemSource
from alvis.sources.gitlab import GitLabSource
from alvis.sources.http import HttpClient


async def test_http_client_streams_with_cap_and_aborts_on_overflow() -> None:
    body = b"x" * 1000

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    client = HttpClient(
        base_url="http://localhost:9000",
        max_bytes=100,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(SourceError) as excinfo:
        await client.request("GET", "/blob", ok_status=(200,), raw=True)
    assert excinfo.value.status_code == 413
    assert "max_bytes=100" in str(excinfo.value)


async def test_http_client_cap_allows_body_within_limit() -> None:
    body = b'{"ok": true}'

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    client = HttpClient(
        base_url="http://localhost:9000",
        max_bytes=1000,
        transport=httpx.MockTransport(handler),
    )
    assert await client.request("GET", "/api") == {"ok": True}
    assert await client.request("GET", "/raw", ok_status=(200,), raw=True) == body


async def test_filesystem_source_skips_oversized_files(tmp_path) -> None:
    (tmp_path / "big.md").write_bytes(b"x" * 500)
    (tmp_path / "small.md").write_text("# A\n\nbody\n", encoding="utf-8")
    source = FilesystemSource(str(tmp_path), max_bytes=300)
    artifacts = await source.fetch()
    uris = [a.uri for a in artifacts]
    assert len(artifacts) == 1
    assert not uris[0].endswith("big.md")


async def test_gitlab_source_skips_oversized_blob() -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/tree"):
            return httpx.Response(
                200,
                json=[
                    {"id": "1", "path": "a.md", "type": "blob"},
                ],
            )
        if request.url.path.endswith("/raw"):
            return httpx.Response(200, content=b"y" * 500)
        return httpx.Response(404)

    source = GitLabSource(
        project="g/p",
        max_bytes=100,
        transport=httpx.MockTransport(handler),
    )
    artifacts = await source.fetch()
    assert artifacts == []
    assert any(p.endswith("/raw") for p in calls)


def test_extract_config_validates_max_bytes() -> None:
    from alvis.config.models import ExtractConfig

    assert ExtractConfig(strategy="auto").max_bytes is None
    assert ExtractConfig(strategy="auto", config={"max_bytes": 1024}).max_bytes == 1024
    with pytest.raises(ValueError, match="max_bytes"):
        ExtractConfig(config={"max_bytes": 0})


async def test_pipeline_skips_oversized_artifact(tmp_path) -> None:
    from alvis.pipeline.runner import run_async

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "big.md").write_bytes(b"z" * 1000)
    (docs / "ok.md").write_text("# T\n\nhello\n", encoding="utf-8")
    config_path = tmp_path / "p.yaml"
    config_path.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {docs}\n"
        "  extract:\n"
        "    config:\n"
        "      max_bytes: 200\n"
        "  index:\n"
        "    type: memory\n",
        encoding="utf-8",
    )
    result = await run_async(config_path)
    assert result.documents_ingested == 1
    assert result.chunks_indexed >= 1