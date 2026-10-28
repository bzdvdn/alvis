"""ProviderSource — the reusable adapter over simple DocumentProviders."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from alvis.sources.base import SourceError
from alvis.sources.provider import DocumentProvider, ProviderSource, SourceDocument


class _Provider:
    name = "test"

    def __init__(self, documents: Sequence[SourceDocument]) -> None:
        self._documents = list(documents)
        self.calls = 0

    async def documents(self) -> Sequence[SourceDocument]:
        self.calls += 1
        return list(self._documents)


class _BrokenProvider:
    name = "broken"

    async def documents(self) -> Sequence[SourceDocument]:
        raise RuntimeError("remote exploded")


def _document(document_id: str, content: str = "body", **overrides: object) -> SourceDocument:
    values: dict[str, object] = {
        "id": document_id,
        "title": f"Title {document_id}",
        "content": content,
        "uri": f"https://example.test/{document_id}",
        "content_type": "text/markdown",
    }
    values.update(overrides)
    return SourceDocument(**values)  # type: ignore[arg-type]


async def test_listing_fingerprints_and_fetch_filters_by_uri() -> None:
    provider = _Provider([_document("a"), _document("b")])
    source = ProviderSource(provider, name="catalog")

    metas = await source.list_documents()
    assert [meta.step_id for meta in metas] == ["a", "b"]
    assert [meta.uri for meta in metas] == [
        "https://example.test/a",
        "https://example.test/b",
    ]
    assert [meta.content_type for meta in metas] == ["text/markdown", "text/markdown"]
    assert provider.calls == 1

    artifacts = await source.fetch(uris={metas[1].uri})
    assert [artifact.step_id for artifact in artifacts] == ["b"]
    assert artifacts[0].uri == "https://example.test/b"
    assert artifacts[0].data == b"body"
    assert artifacts[0].metadata["documentId"] == "b"
    assert artifacts[0].metadata["sourceUrl"] == "https://example.test/b"
    assert artifacts[0].metadata["title"] == "Title b"
    assert artifacts[0].metadata["source"] == "catalog"
    assert provider.calls == 1


async def test_fetch_without_listing_collects_once() -> None:
    provider = _Provider([_document("a")])
    source = ProviderSource([provider])
    artifacts = await source.fetch()
    assert [artifact.step_id for artifact in artifacts] == ["a"]
    assert provider.calls == 1


async def test_fingerprint_tracks_content_title_and_uri_but_not_metadata() -> None:
    async def fingerprint(document: SourceDocument) -> str:
        source = ProviderSource(_Provider([document]))
        return (await source.list_documents())[0].fingerprint

    base = _document("a", metadata={"updatedAtUtc": "2026-01-01T00:00:00Z"})
    assert await fingerprint(base) == await fingerprint(base)
    assert await fingerprint(base) != await fingerprint(_document("a", content="changed"))
    assert await fingerprint(base) != await fingerprint(
        _document("a", title="Different")
    )
    assert await fingerprint(base) != await fingerprint(
        _document("a", uri="https://example.test/other")
    )
    # Volatile metadata (fetch timestamps, versions) must not force a reindex.
    assert await fingerprint(base) == await fingerprint(
        _document("a", metadata={"updatedAtUtc": "2026-10-28T00:00:00Z", "version": 7})
    )


async def test_duplicate_document_ids_across_providers_are_rejected() -> None:
    source = ProviderSource(
        [_Provider([_document("a")]), _Provider([_document("a")])]
    )
    with pytest.raises(SourceError, match="duplicate document id 'a'"):
        await source.list_documents()


async def test_provider_failure_is_a_source_error() -> None:
    source = ProviderSource([_Provider([_document("a")]), _BrokenProvider()], name="kb")
    with pytest.raises(SourceError, match="kb: provider broken failed"):
        await source.fetch()


async def test_documents_over_max_bytes_are_skipped() -> None:
    source = ProviderSource(
        [_Provider([_document("small", "1234"), _document("large", "12345")])],
        max_bytes=4,
    )
    artifacts = await source.fetch()
    assert [artifact.step_id for artifact in artifacts] == ["small"]


async def test_string_content_is_encoded_and_uri_defaults_to_id() -> None:
    source = ProviderSource(
        _Provider(
            [
                SourceDocument(
                    id="page://1",
                    title="Page",
                    content="# Heading",
                    content_type="text/markdown",
                )
            ]
        )
    )
    artifacts = await source.fetch()
    assert artifacts[0].uri == "page://1"
    assert artifacts[0].data == b"# Heading"
    assert "sourceUrl" not in artifacts[0].metadata


@pytest.mark.parametrize("documents", [[], ()])
def test_empty_provider_list_is_rejected(documents: Sequence[object]) -> None:
    with pytest.raises(ValueError, match="at least one provider"):
        ProviderSource(documents)  # type: ignore[arg-type]


def test_protocol_is_runtime_checkable() -> None:
    assert isinstance(_Provider([]), DocumentProvider)


def test_empty_document_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        SourceDocument(id="", content="body")
