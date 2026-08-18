"""Extraction stage — Artifact → Canonical Content Tree (Document)."""

from __future__ import annotations

import html as html_module
import re
from typing import Protocol

from winnow.core.models import Artifact, Document, Section
from winnow.extract.documents import (
    _CONTENT_DOCX,
    _CONTENT_PDF,
    _CONTENT_XLSX,
    DocxExtractor,
    PdfExtractor,
    XlsxExtractor,
)
from winnow.extract.markdown import MarkdownExtractor

_HTML_HEADING_RE = re.compile(r"(?is)<h([1-6])[^>]*>(.*?)</h\1>")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class Extractor(Protocol):
    """Turns an artifact into a canonical document."""

    async def extract(self, artifact: Artifact) -> Document:
        ...


class AutoExtractor:
    """Dispatches to a format extractor based on the artifact content type."""

    async def extract(self, artifact: Artifact) -> Document:
        content_type = artifact.content_type
        if content_type in {"text/markdown", "text/plain"}:
            return await MarkdownExtractor().extract(artifact)
        if content_type == "text/html":
            return _extract_html(artifact)
        if content_type == _CONTENT_PDF:
            return await PdfExtractor().extract(artifact)
        if content_type == _CONTENT_DOCX:
            return await DocxExtractor().extract(artifact)
        if content_type == _CONTENT_XLSX:
            return await XlsxExtractor().extract(artifact)
        raise ValueError(f"unsupported content type: {content_type!r}")


def _extract_html(artifact: Artifact) -> Document:
    text = artifact.data.decode("utf-8", errors="replace")
    parts = _HTML_HEADING_RE.split(text)
    sections: list[Section] = []

    lead = _clean(parts[0])
    if lead:
        sections.append(Section(heading="", body=lead))

    for index in range(1, len(parts), 3):
        heading = _strip_tags(parts[index + 1]).strip()
        body = _clean(parts[index + 2])
        sections.append(Section(heading=heading, body=body))

    title = sections[0].heading if sections else str(
        artifact.metadata.get("title", "")
    )
    return Document(uri=artifact.uri, title=title, sections=tuple(sections))


def _strip_tags(text: str) -> str:
    return html_module.unescape(_HTML_TAG_RE.sub(" ", text))


def _clean(text: str) -> str:
    return " ".join(_strip_tags(text).split())


__all__ = ["Extractor", "AutoExtractor"]