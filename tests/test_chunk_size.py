from __future__ import annotations

from alvis.chunk import SizeChunker
from alvis.core.models import Document, Section


async def test_size_chunker_splits_on_char_budget() -> None:
    body = " ".join(["word"] * 1000)
    doc = Document(uri="u", title="T", sections=(Section(heading="", body=body),))
    chunks = await SizeChunker(max_chars=100, overlap_chars=10).chunk(doc)
    assert len(chunks) > 1
    assert all(len(c.text) <= 100 for c in chunks)
    assert all("word" in c.text for c in chunks)


async def test_size_chunker_cuts_at_word_boundary() -> None:
    body = " ".join(f"token{i}" for i in range(60))
    doc = Document(uri="u", title="T", sections=(Section(heading="", body=body),))
    chunks = await SizeChunker(max_chars=50, overlap_chars=0).chunk(doc)
    joined = " ".join(c.text for c in chunks)
    for i in range(60):
        assert f"token{i}" in joined
    assert all(c.text.strip() == c.text for c in chunks)


async def test_size_chunker_single_chunk_when_small() -> None:
    doc = Document(
        uri="u",
        title="T",
        sections=(Section(heading="H", body="a short body"),),
    )
    chunks = await SizeChunker(max_chars=500, overlap_chars=50).chunk(doc)
    assert len(chunks) == 1
    assert chunks[0].text == "# H\n\na short body"
    assert chunks[0].metadata["heading"] == "H"


async def test_size_chunker_empty_section_is_skipped() -> None:
    doc = Document(uri="u", title="T", sections=(Section(heading="", body=""),))
    assert await SizeChunker().chunk(doc) == []


async def test_size_chunker_overlap_carries_context() -> None:
    body = " ".join(["word"] * 100)
    doc = Document(uri="u", title="T", sections=(Section(heading="", body=body),))
    chunks = await SizeChunker(max_chars=100, overlap_chars=30).chunk(doc)
    assert len(chunks) > 1
    second = chunks[1].text
    first_tail = " ".join(chunks[0].text.split()[-6:])
    assert all(token in second for token in first_tail.split())