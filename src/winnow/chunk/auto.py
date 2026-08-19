"""Chunking stage — Document → chunks."""

from __future__ import annotations

from typing import Protocol

from winnow.core.models import Chunk, Document, Section


class Chunker(Protocol):
    """Splits canonical content into embeddable chunks."""

    async def chunk(self, document: Document) -> list[Chunk]:
        """Split a document into embeddable chunks (contract — see class docstring)."""
        ...


class AutoChunker:
    """Paragraph-aware token-budget chunking.

    Approximates tokens as ``len(text) // 4``; splits on paragraph boundaries
    and carries ``overlap`` trailing tokens between consecutive chunks.
    """

    def __init__(self, max_tokens: int = 500, overlap: int = 50) -> None:
        self.max_tokens = max_tokens
        self.overlap = min(overlap, max_tokens)

    async def chunk(self, document: Document) -> list[Chunk]:
        """Split a document's sections into token-budgeted chunks."""
        chunks: list[Chunk] = []
        for section in document.sections:
            text = _section_text(document, section)
            for part in _split_text(text, self.max_tokens, self.overlap):
                if not part.strip():
                    continue
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


def _section_text(document: Document, section: Section) -> str:
    if not section.body:
        return ""
    if not section.heading:
        return section.body
    return f"# {section.heading}\n\n{section.body}"


def _split_text(text: str, max_tokens: int, overlap: int) -> list[str]:
    budget = max_tokens * 4
    overlap_chars = overlap * 4
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []

    if not paragraphs:
        return chunks

    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > budget:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(paragraph, budget))
            continue
        if current and len(current) + 2 + len(paragraph) > budget:
            chunks.append(current)
            current = _tail(current, overlap_chars)
        current = f"{current}\n\n{paragraph}" if current else paragraph

    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]


def _hard_split(text: str, budget: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > budget:
            pieces.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        pieces.append(current)
    return pieces


def _tail(text: str, chars: int) -> str:
    if len(text) <= chars:
        return ""
    window = text[-chars:].lstrip()
    boundary = window.find(" ")
    if boundary > 0:
        window = window[boundary:].lstrip()
    return window


__all__ = ["AutoChunker", "Chunker"]