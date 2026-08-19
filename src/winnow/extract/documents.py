"""Binary document extractors — PDF, DOCX, XLSX → Canonical Content Tree.

Optional dependencies (``pip install winnow[documents]``): ``pypdf``,
``python-docx``, ``openpyxl``. Availability is probed at import time and
the libraries are imported lazily, so a minimal install never loads them.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io

from winnow.core.models import Artifact, Document, Section

_CONTENT_PDF = "application/pdf"
_CONTENT_DOCX = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_CONTENT_XLSX = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

_domains = {
    "pypdf": importlib.util.find_spec("pypdf") is not None,
    "docx": importlib.util.find_spec("docx") is not None,
    "openpyxl": importlib.util.find_spec("openpyxl") is not None,
}
_DOCUMENTS_EXTRA = "install the optional extra: pip install winnow[documents]"


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _make_document(artifact: Artifact, title: str, sections: list[Section]) -> Document:
    resolved = title or str(artifact.metadata.get("title", ""))
    return Document(uri=artifact.uri, title=resolved, sections=tuple(sections))


class PdfExtractor:
    """Extracts per-page text from a PDF (pypdf).

    Parsing runs in a worker thread (``asyncio.to_thread``) so the event loop
    is never blocked. PDFs need random access, so the full bytes are parsed;
    upstream artifacts are capped by the pipeline's ``max_bytes`` budget.
    """

    def _parse(self, data: bytes) -> tuple[str, list[Section]]:
        if not _domains["pypdf"]:
            raise ValueError(f"PDF extraction requires pypdf ({_DOCUMENTS_EXTRA})")
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        sections: list[Section] = []
        for index, page in enumerate(reader.pages):
            text = _collapse(page.extract_text() or "")
            if text:
                sections.append(Section(heading=f"Page {index + 1}", body=text))
        title = ""
        try:
            metadata = getattr(reader, "metadata", None)
            title = str(metadata.title or "") if metadata else ""
        except Exception:  # noqa: BLE001 - metadata parsing varies by PDF
            title = ""
        return title, sections

    async def extract(self, artifact: Artifact) -> Document:
        title, sections = await asyncio.to_thread(self._parse, artifact.data)
        return _make_document(artifact, title, sections)


class DocxExtractor:
    """Extracts headings and body text from a Word document (python-docx).

    Parsing runs in a worker thread; as with PDF, the whole (capped) file is
    parsed because DOCX is a zip needing random access.
    """

    def _parse(self, data: bytes) -> tuple[str, list[Section]]:
        if not _domains["docx"]:
            raise ValueError(
                f"DOCX extraction requires python-docx ({_DOCUMENTS_EXTRA})"
            )
        from docx import Document

        document = Document(io.BytesIO(data))
        sections: list[Section] = []
        heading = ""
        buffer: list[str] = []

        def flush() -> None:
            nonlocal heading, buffer
            body = _collapse(" ".join(buffer))
            if heading or body:
                sections.append(Section(heading=heading, body=body))
            heading, buffer = "", []

        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            style_name = paragraph.style.name if paragraph.style else ""
            if style_name.startswith("Heading"):
                flush()
                heading = text
            else:
                buffer.append(text)
        flush()
        return "", sections

    async def extract(self, artifact: Artifact) -> Document:
        title, sections = await asyncio.to_thread(self._parse, artifact.data)
        return _make_document(artifact, title, sections)


class XlsxExtractor:
    """Extracts each worksheet as a ``<sheet title>`` section (openpyxl).

    Workbooks are opened ``read_only=True`` and rows streamed lazily with
    ``iter_rows``, so large files never materialise in memory — the event
    loop stays responsive via ``asyncio.to_thread``.
    """

    def _parse(self, data: bytes) -> tuple[str, list[Section]]:
        if not _domains["openpyxl"]:
            raise ValueError(
                f"XLSX extraction requires openpyxl ({_DOCUMENTS_EXTRA})"
            )
        from openpyxl import load_workbook  # type: ignore[import-untyped]

        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sections: list[Section] = []
        try:
            for worksheet in workbook.worksheets:
                rows: list[str] = []
                for row in worksheet.iter_rows(values_only=True):
                    cells = [str(cell).strip() for cell in row if cell is not None]
                    if cells:
                        rows.append(" | ".join(cells))
                body = "\n".join(rows).strip()
                if body:
                    sections.append(Section(heading=worksheet.title, body=body))
        finally:
            workbook.close()
        return "", sections

    async def extract(self, artifact: Artifact) -> Document:
        title, sections = await asyncio.to_thread(self._parse, artifact.data)
        return _make_document(artifact, title, sections)


__all__ = [
    "_CONTENT_DOCX",
    "_CONTENT_PDF",
    "_CONTENT_XLSX",
    "DocxExtractor",
    "PdfExtractor",
    "XlsxExtractor",
]