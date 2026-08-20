"""Golden tests: one committed snapshot per connector behaviour.

These exercise the contract every connector must satisfy — ``fetch()``
returns ``Artifact`` objects with stable ``step_id``/``uri``/``content_type``/
``data``/``metadata`` — against canned service responses, and diff the result
against a committed snapshot in ``tests/golden/``. Run read-only in CI with
``pytest -m golden``; re-baseline locally with ``ALVIS_ACCEPT=1``.
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from alvis.sources import (
    ConfluenceSource,
    FilesystemSource,
    GitHubSource,
    GitLabSource,
    S3Source,
    StaticUrlSource,
)
from alvis.testing import MockServer, artifacts_snapshot, assert_golden

_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def _s3_xml(keys: list[str], *, truncated: bool = False) -> bytes:
    root = ET.Element(_S3_NS + "ListBucketResult")
    for key in keys:
        contents = ET.SubElement(root, _S3_NS + "Contents")
        ET.SubElement(contents, _S3_NS + "Key").text = key
    ET.SubElement(root, _S3_NS + "IsTruncated").text = "true" if truncated else "false"
    return ET.tostring(root, encoding="utf-8")


@pytest.mark.golden
async def test_golden_fs() -> None:
    corpus = Path(__file__).parent / "fixtures" / "corpus"
    source = FilesystemSource(path=str(corpus))
    artifacts = await source.fetch()
    assert_golden("fs", artifacts_snapshot(artifacts, root=str(corpus.resolve())))


@pytest.mark.golden
async def test_golden_confluence() -> None:
    server = MockServer()
    server.on(
        "GET",
        "/rest/api/content",
        json_payload={
            "results": [
                {
                    "id": "100",
                    "title": "Alvis Overview",
                    "_links": {"webui": "/pages/viewpage.action?pageId=100"},
                    "version": {"number": 3},
                },
                {
                    "id": "101",
                    "title": "Chunking Strategy Guide",
                    "_links": {"webui": "/pages/viewpage.action?pageId=101"},
                    "version": {"number": 1},
                },
            ],
            "size": 2,
        },
    )
    server.on(
        "GET",
        "/rest/api/content/100",
        json_payload={
            "id": "100",
            "title": "Alvis Overview",
            "version": {"number": 3},
            "body": {
                "storage": {
                    "value": "<h1>Alvis Overview</h1><p>Alvis is a no-code "
                    "knowledge ingestion engine.</p>"
                }
            },
        },
    )
    server.on(
        "GET",
        "/rest/api/content/101",
        json_payload={
            "id": "101",
            "title": "Chunking Strategy Guide",
            "version": {"number": 1},
            "body": {
                "storage": {
                    "value": "<h1>Chunking Strategy Guide</h1><p>Documents are "
                    "split into overlapping chunks.</p>"
                }
            },
        },
    )

    source = ConfluenceSource(url="http://localhost:8080", space="TEAM")
    source.client.transport = server.transport

    artifacts = await source.fetch()
    assert_golden("confluence", artifacts_snapshot(artifacts))


@pytest.mark.golden
async def test_golden_github() -> None:
    server = MockServer()
    server.on(
        "GET",
        "/repos/acme/kb/git/trees/main",
        json_payload={
            "tree": [
                {"type": "blob", "path": "docs/intro.md", "sha": "abc123"},
                {"type": "blob", "path": "logo.png", "sha": "png111"},
            ]
        },
    )
    server.on(
        "GET",
        "/repos/acme/kb/contents/docs/intro.md",
        json_payload={
            "content": base64.b64encode(b"# Intro from GitHub").decode(),
            "html_url": "https://github.com/acme/kb/blob/main/docs/intro.md",
        },
    )

    source = GitHubSource(repo="acme/kb", branch="main", transport=server.transport)
    artifacts = await source.fetch()
    assert_golden("github", artifacts_snapshot(artifacts))


@pytest.mark.golden
async def test_golden_gitlab() -> None:
    server = MockServer()
    server.on(
        "GET",
        "/api/v4/projects/group%2Fkb/repository/tree",
        json_payload=[
            {"id": "blob-a", "type": "blob", "path": "docs/guide.md"},
            {"id": "blob-b", "type": "blob", "path": "logo.png"},
        ],
    )
    server.on(
        "GET",
        "/api/v4/projects/group%2Fkb/repository/blobs/blob-a/raw",
        content=b"# Guide from GitLab",
    )

    source = GitLabSource(
        project="group/kb",
        branch="main",
        url="https://gitlab.example.com",
        transport=server.transport,
    )
    artifacts = await source.fetch()
    assert_golden("gitlab", artifacts_snapshot(artifacts))


@pytest.mark.golden
async def test_golden_s3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_ACCESS", "minioadmin")
    monkeypatch.setenv("S3_SECRET", "minioadmin")

    server = MockServer()
    server.on("GET", "/kb", content=_s3_xml(["docs/intro.md", "logo.png"]))
    server.on("GET", "/kb/docs/intro.md", content=b"# Intro from S3")

    source = S3Source(
        url="http://localhost:9000",
        bucket="kb",
        access_key_env="S3_ACCESS",
        secret_key_env="S3_SECRET",
        transport=server.transport,
    )
    artifacts = await source.fetch()
    assert_golden("s3", artifacts_snapshot(artifacts))


@pytest.mark.golden
async def test_golden_static_url() -> None:
    server = MockServer()
    server.on(
        "GET",
        "/",
        content=(
            b"<html><head><title>Alvis Docs</title></head>"
            b"<body><h1>Static URL source</h1><p>Plain HTML ingesting.</p></body></html>"
        ),
    )
    server.on(
        "GET",
        "/about",
        content=(
            b"<html><head><title>About</title></head>"
            b"<body><h1>About Alvis</h1></body></html>"
        ),
    )

    source = StaticUrlSource(
        urls=["https://docs.example.com/", "https://docs.example.com/about"],
        transport=server.transport,
    )
    artifacts = await source.fetch()
    assert_golden("static_url", artifacts_snapshot(artifacts))
