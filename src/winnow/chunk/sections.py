"""Heading-anchored chunking — one chunk per section (``sections`` strategy).

Long sections are split on the token budget, and every continuation piece
keeps the heading as context so fragments stay retrievable on their own.
"""

from __future__ import annotations

from winnow.chunk.auto import _section_text, _split_text
from winnow.core.models import Chunk, Document


class SectionsChunker:
    """Produces a chunk per ``Document`` section, aligned to headings.

    Use this strategy when the source document is naturally structured
    (docs sites, Confluence, Word exports). Chunks map 1:1 to headings, and
    oversized sections are split with the heading repeated as context.
    """

    def __init__(self, max_tokens: int = 500, overlap: int = 50) -> None:
        self.max_tokens = max_tokens
        self.overlap = min(overlap, max_tokens)

    async def chunk(self, document: Document) -> list[Chunk]:
        """Produce one chunk per section, repeating the heading on continuations."""
        chunks: list[Chunk] = []
        for section in document.sections:
            text = _section_text(document, section)
            for index, part in enumerate(_split_text(text, self.max_tokens, self.overlap)):
                if (
                    index > 0
                    and section.heading
                    and not part.startswith(f"# {section.heading}")
                ):
                    part = f"# {section.heading}\n\n{part}"
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


__all__ = ["SectionsChunker"]