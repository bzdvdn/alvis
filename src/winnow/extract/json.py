"""Structured JSON extractor — JSON → Canonical Content Tree sections.

The document is flattened so every scalar value becomes a section whose
heading is its dotted path (``user.address.city``), and array elements are
addressed by index (``items.0``). This keeps each fact independently
retrievable while preserving the document's structure in the headings.

Parsing is memory-safe within the pipeline's ``max_bytes`` artifact budget:
the payload is capped at fetch time, so a huge JSON file is skipped before
it is ever decoded here.
"""

from __future__ import annotations

import json

from winnow.core.models import Artifact, Document, Section


class JsonExtractor:
    """Converts JSON into heading-anchored sections (``application/json``)."""

    async def extract(self, artifact: Artifact) -> Document:
        text = artifact.data.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {artifact.uri}: {exc}") from exc
        sections: list[Section] = []
        _flatten(data, [], sections)
        if not sections and data is not None:
            sections.append(Section(heading="", body=str(data)))
        title = str(artifact.metadata.get("title", ""))
        return Document(uri=artifact.uri, title=title, sections=tuple(sections))


def _flatten(value: object, path: list[str], sections: list[Section]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(item, path + [str(key)], sections)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _flatten(item, path + [str(index)], sections)
    else:
        body = str(value) if value is not None else ""
        if body:
            sections.append(Section(heading=".".join(path), body=body))


__all__ = ["JsonExtractor"]