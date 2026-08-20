"""Structured CSV extractor — one heading-anchored section per row.

Rows are consumed lazily from a :class:`io.StringIO` through ``csv.reader``
(no materialisation of the whole row set), mirroring the XLSX extractor's
``read_only`` streaming. Memory is bounded by the largest row and the
accumulated sections, both inside the pipeline's ``max_bytes`` cap.

Section layout:

- no header row → ``Row N`` heading, cells ``|``-joined;
- header row (first row of non-empty strings) → ``Row N`` heading, body as
  ``header: value | ...`` pairs so each fact stays machine-readable.
"""

from __future__ import annotations

import csv
import io

from alvis.core.models import Artifact, Document, Section


class CsvExtractor:
    """Converts CSV into row sections (content type ``text/csv``)."""

    async def extract(self, artifact: Artifact) -> Document:
        """Parse a CSV artifact into one ``Row N`` section per line."""
        text = artifact.data.decode("utf-8", errors="replace")
        title = str(artifact.metadata.get("title", ""))
        if not text.strip():
            return Document(uri=artifact.uri, title=title, sections=())

        reader = csv.reader(io.StringIO(text))
        header: list[str] | None = None
        sections: list[Section] = []
        row_index = 0
        for raw_row in reader:
            cells = [cell.strip() for cell in raw_row]
            if not any(cells):
                continue
            if row_index == 0 and _looks_like_header(cells):
                header = cells
                row_index += 1
                continue
            row_index += 1
            if header is None:
                body = " | ".join(cell for cell in cells if cell)
            else:
                body = " | ".join(
                    f"{field}: {cell}"
                    for field, cell in zip(header, cells, strict=False)
                    if field
                )
            sections.append(Section(heading=f"Row {row_index}", body=body))
        return Document(uri=artifact.uri, title=title, sections=tuple(sections))


def _looks_like_header(row: list[str]) -> bool:
    if len(row) < 2:
        return False
    if not all(bool(cell) for cell in row):
        return False
    return all(any(character.isalpha() for character in cell) for cell in row)


__all__ = ["CsvExtractor"]