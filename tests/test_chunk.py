from __future__ import annotations

from winnow.chunk import AutoChunker
from winnow.core.models import Document, Section


async def test_chunker_splits_long_document() -> None:
    body = " ".join(["paragraph word"] * 400)
    doc = Document(
        uri="https://x/doc",
        title="Title",
        sections=(Section(heading="", body=body),),
    )
    chunks = await AutoChunker(max_tokens=100, overlap=20).chunk(doc)
    assert len(chunks) > 1
    assert all(chunk.source_uri == doc.uri for chunk in chunks)
    assert all(len(chunk.text) <= 100 * 4 + 20 * 4 + 200 for chunk in chunks)


async def test_chunker_keeps_heading_in_metadata() -> None:
    doc = Document(
        uri="u",
        title="T",
        sections=(Section(heading="Setup", body="some content"),),
    )
    chunks = await AutoChunker().chunk(doc)
    assert chunks[0].metadata["heading"] == "Setup"
    assert "Setup" in chunks[0].text


async def test_chunker_empty_document() -> None:
    doc = Document(uri="u", title="T", sections=(Section(heading="", body=""),))
    assert await AutoChunker().chunk(doc) == []