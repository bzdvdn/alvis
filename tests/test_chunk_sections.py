from __future__ import annotations

from winnow.chunk import SectionsChunker
from winnow.core.models import Document, Section


async def test_sections_chunker_one_chunk_per_section() -> None:
    doc = Document(
        uri="https://x/doc",
        title="T",
        sections=(
            Section(heading="Intro", body="small body"),
            Section(heading="Details", body="another body"),
        ),
    )
    chunks = await SectionsChunker().chunk(doc)
    assert [c.metadata["heading"] for c in chunks] == ["Intro", "Details"]
    assert [c.text for c in chunks] == [
        "# Intro\n\nsmall body",
        "# Details\n\nanother body",
    ]


async def test_sections_chunker_continues_heading_on_budget_split() -> None:
    body = " word ".join(["paragraph"] * 400)
    doc = Document(
        uri="u",
        title="T",
        sections=(Section(heading="Long Section", body=body),),
    )
    chunks = await SectionsChunker(max_tokens=100, overlap=20).chunk(doc)
    assert len(chunks) > 1
    assert all(c.metadata["heading"] == "Long Section" for c in chunks)
    assert "# Long Section" in chunks[0].text
    assert "# Long Section" in chunks[-1].text
    assert all(len(c.text) <= 100 * 4 + 20 * 4 + 64 for c in chunks)


async def test_sections_chunker_empty_section_is_skipped() -> None:
    doc = Document(
        uri="u",
        title="T",
        sections=(Section(heading="Empty", body=""),),
    )
    assert await SectionsChunker().chunk(doc) == []


async def test_sections_chunker_single_chunk_within_budget() -> None:
    doc = Document(
        uri="u",
        title="T",
        sections=(Section(heading="H", body="one two three"),),
    )
    chunks = await SectionsChunker().chunk(doc)
    assert len(chunks) == 1
    assert chunks[0].text == "# H\n\none two three"