from __future__ import annotations

import base64
import xml.etree.ElementTree as ET

import httpx
import pytest

from alvis.sources.base import SourceError
from alvis.sources.github import GitHubSource
from alvis.sources.gitlab import GitLabSource
from alvis.sources.s3 import S3Source

_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


async def test_github_fetches_blobs_and_filters() -> None:
    tree = {
        "tree": [
            {"type": "tree", "path": "docs"},
            {"type": "blob", "path": "docs/intro.md", "sha": "abc"},
            {"type": "blob", "path": "images/logo.png", "sha": "img"},
            {"type": "blob", "path": "code/app.py", "sha": "py1"},
            {"type": "blob", "path": "docs/guide.markdown", "sha": "mk1"},
        ]
    }

    def encoded(text: str) -> str:
        return base64.b64encode(text.encode()).decode()

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/git/trees/" in path:
            return httpx.Response(200, json=tree)
        if "intro.md" in path:
            return httpx.Response(
                200,
                json={
                    "content": encoded("# Intro"),
                    "html_url": "https://github.com/acme/kb/blob/main/docs/intro.md",
                },
            )
        if "app.py" in path:
            return httpx.Response(200, json={"content": encoded("def f():\n    pass")})
        if "guide.markdown" in path:
            return httpx.Response(200, json={"content": encoded("Guide")})
        return httpx.Response(500, json={})

    source = GitHubSource(
        repo="acme/kb",
        branch="main",
        transport=httpx.MockTransport(handler),
    )
    artifacts = await source.fetch()
    assert [a.metadata["path"] for a in artifacts] == [
        "docs/intro.md",
        "code/app.py",
        "docs/guide.markdown",
    ]
    assert artifacts[0].data == b"# Intro"
    assert artifacts[0].uri == "https://github.com/acme/kb/blob/main/docs/intro.md"
    assert {a.content_type for a in artifacts} == {
        "text/markdown",
        "text/plain",
    }
    assert artifacts[0].metadata["documentId"] == "abc"


async def test_github_include_globs() -> None:
    tree = {"tree": [{"type": "blob", "path": "docs/x.md", "sha": "a"}]}

    async def handler(request: httpx.Request) -> httpx.Response:
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json=tree)
        return httpx.Response(200, json={"content": base64.b64encode(b"x").decode()})

    source = GitHubSource(
        repo="acme/kb",
        include_globs=["docs/**"],
        transport=httpx.MockTransport(handler),
    )
    assert len(await source.fetch()) == 1


async def test_github_no_text_matches() -> None:
    tree = {"tree": [{"type": "blob", "path": "logo.png", "sha": "a"}]}

    async def handler(request: httpx.Request) -> httpx.Response:
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json=tree)
        return httpx.Response(404, json={})

    source = GitHubSource(repo="acme/kb", transport=httpx.MockTransport(handler))
    assert await source.fetch() == []


async def test_gitlab_fetches_raw_blobs_and_encodes_project() -> None:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        path = request.url.path
        if path.endswith("/repository/tree"):
            return httpx.Response(
                200,
                json=[
                    {"id": "a", "type": "blob", "path": "docs/intro.md"},
                    {"id": "b", "type": "blob", "path": "logo.png"},
                ],
            )
        if path.endswith("/blobs/a/raw"):
            return httpx.Response(200, content=b"# Self hosted")
        if path.endswith("/blobs/b/raw"):
            return httpx.Response(200, content=b"\x89PNG")
        return httpx.Response(500, json={})

    source = GitLabSource(
        project="group/kb",
        branch="main",
        url="https://gitlab.example.com",
        transport=httpx.MockTransport(handler),
    )
    artifacts = await source.fetch()
    assert len(artifacts) == 1
    assert artifacts[0].data == b"# Self hosted"
    assert artifacts[0].metadata["path"] == "docs/intro.md"
    assert artifacts[0].metadata["documentId"] == "a"
    assert artifacts[0].uri == (
        "https://gitlab.example.com/group/kb/-/blob/main/docs/intro.md"
    )
    assert any("/projects/group%2Fkb/repository/tree" in p for p in seen)
    assert any("?ref=" in p for p in seen)


async def test_gitlab_pagination() -> None:
    page_one = [{"id": f"s{i}", "type": "blob", "path": f"f{i}.md"} for i in range(2)]

    async def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        if page == 1:
            return httpx.Response(200, json=page_one)
        return httpx.Response(200, json=[{"id": "e", "type": "blob", "path": "extra.md"}])

    source = GitLabSource(
        project="grp/p",
        per_page=2,
        transport=httpx.MockTransport(handler),
    )
    artifacts = await source.fetch()
    assert len(artifacts) == 3
    assert [a.metadata["path"] for a in artifacts] == ["f0.md", "f1.md", "extra.md"]
    assert [a.metadata["documentId"] for a in artifacts] == ["s0", "s1", "e"]


def _s3_xml(keys: list[str], *, truncated: bool, token: str | None = None) -> bytes:
    root = ET.Element(_S3_NS + "ListBucketResult")
    for key in keys:
        contents = ET.SubElement(root, _S3_NS + "Contents")
        ET.SubElement(contents, _S3_NS + "Key").text = key
    ET.SubElement(root, _S3_NS + "IsTruncated").text = (
        "true" if truncated else "false"
    )
    if token:
        ET.SubElement(root, _S3_NS + "NextContinuationToken").text = token
    return ET.tostring(root, encoding="utf-8")


async def test_s3_sigv4_fetch_with_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("S3_ACCESS", "minioadmin")
    monkeypatch.setenv("S3_SECRET", "minioadmin")
    signed: list[dict[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        signed.append(dict(request.headers))
        path = request.url.path
        if request.url.params.get("list-type") == "2":
            if request.url.params.get("continuation-token"):
                body = _s3_xml(["docs/extra.md"], truncated=False)
            else:
                body = _s3_xml(
                    ["docs/intro.md", "logo.png"], truncated=True, token="tok-1"
                )
            return httpx.Response(200, content=body)
        if path.endswith("/docs/intro.md"):
            return httpx.Response(200, content=b"# From bucket")
        if path.endswith("/docs/extra.md"):
            return httpx.Response(200, content=b"## Extra")
        if path.endswith("/logo.png"):
            return httpx.Response(200, content=b"\x89PNG")
        return httpx.Response(500, content=b"")

    source = S3Source(
        url="http://localhost:9000",
        bucket="kb",
        access_key_env="S3_ACCESS",
        secret_key_env="S3_SECRET",
        transport=httpx.MockTransport(handler),
    )
    artifacts = await source.fetch()
    assert [a.metadata["path"] for a in artifacts] == [
        "docs/intro.md",
        "docs/extra.md",
    ]
    assert artifacts[0].data == b"# From bucket"
    assert artifacts[1].data == b"## Extra"

    auth = signed[0]["authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=minioadmin/")
    assert "aws/aws4_request" in auth or "s3/aws4_request" in auth
    assert signed[0]["x-amz-date"].startswith("20")


async def test_s3_prefix_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_ACCESS", "minioadmin")
    monkeypatch.setenv("S3_SECRET", "minioadmin")

    async def handler(request: httpx.Request) -> httpx.Response:
        body = _s3_xml(["kb/docs/intro.md"], truncated=False)
        return httpx.Response(200, content=body)

    source = S3Source(
        url="http://localhost:9000",
        bucket="kb",
        prefix="kb/docs",
        access_key_env="S3_ACCESS",
        secret_key_env="S3_SECRET",
        transport=httpx.MockTransport(handler),
    )
    assert len(await source.fetch()) == 1


async def test_s3_prefix_limits_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_ACCESS", "minioadmin")
    monkeypatch.setenv("S3_SECRET", "minioadmin")
    seen_prefixes: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("list-type") == "2":
            seen_prefixes.append(request.url.params.get("prefix"))
            return httpx.Response(
                200, content=_s3_xml(["kb/docs/intro.md"], truncated=False)
            )
        return httpx.Response(200, content=b"# Intro")

    source = S3Source(
        url="http://localhost:9000",
        bucket="kb",
        prefix="kb/docs",
        access_key_env="S3_ACCESS",
        secret_key_env="S3_SECRET",
        transport=httpx.MockTransport(handler),
    )
    await source.fetch()
    assert seen_prefixes == ["kb/docs"]


async def test_s3_exclude_globs_skips_videos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("S3_ACCESS", "minioadmin")
    monkeypatch.setenv("S3_SECRET", "minioadmin")

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.params.get("list-type") == "2":
            body = _s3_xml(
                ["docs/intro.md", "videos/demo.mp4", "docs/screen.mov"],
                truncated=False,
            )
            return httpx.Response(200, content=body)
        if path.endswith("/docs/intro.md"):
            return httpx.Response(200, content=b"# Intro")
        return httpx.Response(200, content=b"binary")

    source = S3Source(
        url="http://localhost:9000",
        bucket="kb",
        include_globs=["docs/**", "videos/**"],
        exclude_globs=["**/*.mp4", "**/*.mov"],
        access_key_env="S3_ACCESS",
        secret_key_env="S3_SECRET",
        transport=httpx.MockTransport(handler),
    )
    artifacts = await source.fetch()
    assert [a.metadata["path"] for a in artifacts] == ["docs/intro.md"]


def test_s3_missing_credentials() -> None:
    with pytest.raises(SourceError):
        S3Source(
            url="http://localhost:9000",
            bucket="kb",
            access_key_env="NOPE_1",
            secret_key_env="NOPE_2",
        )