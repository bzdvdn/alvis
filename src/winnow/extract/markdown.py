"""Markdown/text extraction.

Artifact → Document: heading lines become section anchors.
"""

from __future__ import annotations

from pathlib import Path

from winnow.core.models import Artifact, Document, Section


class MarkdownExtractor:
    """Splits plain text on heading lines (``#``..``######``)."""

    async def extract(self, artifact: Artifact) -> Document:
        text = artifact.data.decode("utf-8", errors="replace")
        sections: list[Section] = []
        current_heading = ""
        current_body: list[str] = []
        first_heading = ""

        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            heading = _heading_level(stripped)
            if heading is not None:
                if current_heading or current_body:
                    sections.append(
                        Section(
                            heading=current_heading,
                            body="\n".join(current_body).strip(),
                        )
                    )
                if not first_heading:
                    first_heading = stripped[heading:].strip()
                current_heading = stripped[heading:].strip()
                current_body = []
            else:
                current_body.append(raw_line)

        sections.append(
            Section(heading=current_heading, body="\n".join(current_body).strip())
        )
        title = first_heading or str(
            artifact.metadata.get("title")
            or Path(artifact.uri).stem
            or "untitled"
        )
        return Document(uri=artifact.uri, title=title, sections=tuple(sections))


def _heading_level(line: str) -> int | None:
    hashes = 0
    for char in line:
        if char == "#":
            hashes += 1
        else:
            break
    if 1 <= hashes <= 6 and len(line) > hashes and line[hashes] == " ":
        return hashes
    return None