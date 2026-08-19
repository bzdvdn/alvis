"""Phase-2 incremental listing: fingerprints from listing, no body download.

Each connector that can fingerprint documents without a body download
implements ``list_documents`` and ``fetch(uris=...)``. These tests prove the
contract: listing makes only the cheap scan call, and ``fetch(uris)``
downloads exactly the requested URIs.
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET

import pytest

from winnow.sources import ConfluenceSource, GitHubSource, GitLabSource, S3Source
from winnow.testing import MockServer

_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def _s3_xml(objects: list[tuple[str, str]]) -> bytes:
    root = ET.Element(_S3_NS + "ListBucketResult")
    for key, etag in objects:
        contents = ET.SubElement(root, _S3_NS + "Contents")
        ET.SubElement(contents, _S3_NS + "Key").text = key
        ET.SubElement(contents, _S3_NS + "ETag").text = etag
    ET.SubElement(root, _S3_NS + "IsTruncated").text = "false"
    return ET.tostring(root, encoding="utf-8")


async def test_s3_listing_fingerprints_etag_and_fetch_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("S3_ACCESS", "minioadmin")
    monkeypatch.setenv("S3_SECRET", "minioadmin")
    server = MockServer()
    server.on("GET", "/kb", content=_s3_xml([("docs/a.md", '"e1"'), ("docs/b.md", '"e2"')]))
    server.on("GET", "/kb/docs/b.md", content=b"# B body")

    source = S3Source(
        url="http://localhost:9000",
        bucket="kb",
        access_key_env="S3_ACCESS",
        secret_key_env="S3_SECRET",
        transport=server.transport,
    )
    metas = await source.list_documents()
    assert {m.fingerprint for m in metas} == {"e1", "e2"}
    assert not [r for r in server.requests if r.url.path not in ("/kb",)]

    artifacts = await source.fetch(uris={metas[1].uri})
    assert len(artifacts) == 1
    assert artifacts[0].data == b"# B body"
    assert artifacts[0].uri == metas[1].uri


async def test_gitlab_listing_fingerprints_blob_sha() -> None:
    server = MockServer()
    server.on(
        "GET",
        "/api/v4/projects/group%2Fkb/repository/tree",
        json_payload=[
            {"id": "sha-1", "type": "blob", "path": "docs/a.md"},
            {"id": "sha-2", "type": "blob", "path": "docs/b.md"},
        ],
    )
    server.on(
        "GET",
        "/api/v4/projects/group%2Fkb/repository/blobs/sha-2/raw",
        content=b"# B",
    )

    source = GitLabSource(
        project="group/kb",
        branch="main",
        url="https://gitlab.example.com",
        transport=server.transport,
    )
    metas = await source.list_documents()
    assert [m.fingerprint for m in metas] == ["sha-1", "sha-2"]
    assert not [r for r in server.requests if "/blobs/" in r.url.path]

    artifacts = await source.fetch(uris={metas[1].uri})
    assert len(artifacts) == 1
    assert artifacts[0].step_id == "sha-2"


async def test_github_listing_fingerprints_blob_sha() -> None:
    server = MockServer()
    server.on(
        "GET",
        "/repos/acme/kb/git/trees/main",
        json_payload={
            "tree": [
                {"type": "blob", "path": "docs/a.md", "sha": "abc1"},
                {"type": "blob", "path": "docs/b.md", "sha": "abc2"},
            ]
        },
    )
    server.on(
        "GET",
        "/repos/acme/kb/contents/docs/b.md",
        json_payload={
            "content": base64.b64encode(b"# B").decode(),
            "html_url": "https://github.com/acme/kb/blob/main/docs/b.md",
        },
    )

    source = GitHubSource(repo="acme/kb", branch="main", transport=server.transport)
    metas = await source.list_documents()
    assert [m.fingerprint for m in metas] == ["abc1", "abc2"]
    assert not [r for r in server.requests if "/contents/" in r.url.path]

    assert (
        await source.fetch(
            uris={"https://github.com/acme/kb/blob/main/docs/missing.md"}
        )
        == []
    )
    artifacts = await source.fetch(uris={metas[1].uri})
    assert [a.step_id for a in artifacts] == ["abc2"]


async def test_confluence_listing_fingerprints_page_version() -> None:
    server = MockServer()
    server.on(
        "GET",
        "/rest/api/content",
        json_payload={
            "results": [
                {
                    "id": "100",
                    "title": "A",
                    "_links": {"webui": "/pages/viewpage.action?pageId=100"},
                    "version": {"number": 3},
                },
                {
                    "id": "101",
                    "title": "B",
                    "_links": {"webui": "/pages/viewpage.action?pageId=101"},
                    "version": {"number": 1},
                },
            ]
        },
    )
    server.on(
        "GET",
        "/rest/api/content/100",
        json_payload={
            "body": {"storage": {"value": "<h1>A</h1><p>body</p>"}},
        },
    )

    source = ConfluenceSource(url="http://localhost:8080", space="TEAM")
    source.client.transport = server.transport
    metas = await source.list_documents()
    assert [m.fingerprint for m in metas] == ["v3", "v1"]
    assert not [r for r in server.requests if "/content/100" in r.url.path]

    artifacts = await source.fetch(uris={metas[0].uri})
    assert [a.metadata["id"] for a in artifacts] == ["100"]