"""Size-budget chunking — pure character slices, no heading knowledge.

Use this strategy for unstructured sources (logs, dumps, export files) where
paragraph and heading structure are absent or unreliable: text is split on a
fixed character budget at the nearest word boundary, with an optional
overlap so no meaning is lost across boundaries.
"""

from __future__ import annotations

from winnow.chunk.auto import _section_text
from winnow.core.models import Chunk, Document


class SizeChunker:
    """Character-budget chunker (strategy ``size``)."""

    def __init__(self, max_chars: int = 2000, overlap_chars: int = 200) -> None:
        self.max_chars = max_chars
        self.overlap_chars = min(overlap_chars, max_chars // 2)

    async def chunk(self, document: Document) -> list[Chunk]:
        chunks: list[Chunk] = []
        for section in document.sections:
            text = _section_text(document, section)
            for part in _split_by_chars(text, self.max_chars, self.overlap_chars):
                chunks.append(
                    Chunk(
                        text=part,
                        source_uri=document.uri,
                        metadata={
                            "title": document.title,
                            "heading": section.heading,
                        },
                    )
                )
        return chunks


def _split_by_chars(text: str, size: int, overlap_chars: int) -> list[str]:
    size = max(1, size)
    if len(text) <= size:
        return [text] if text.strip() else []
    parts: list[str] = []
    position = 0
    length = len(text)
    while position < length:
        end = min(position + size, length)
        if end < length:
            boundary = text.rfind(" ", position, end)
            if boundary > position:
                end = boundary
        part = text[position:end]
        if part.strip():
            parts.append(part)
        if end >= length:
            break
        next_position = end - overlap_chars if overlap_chars > 0 else end
        if next_position <= position:
            next_position = end
        position = next_position
        while position < length and text[position] in " \t\n":
            position += 1
    return parts


__all__ = ["SizeChunker"]