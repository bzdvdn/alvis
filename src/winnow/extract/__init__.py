"""Extraction stage — artifacts → canonical documents."""

from winnow.extract.base import AutoExtractor, Extractor
from winnow.extract.documents import DocxExtractor, PdfExtractor, XlsxExtractor
from winnow.extract.markdown import MarkdownExtractor

__all__ = [
    "AutoExtractor",
    "DocxExtractor",
    "Extractor",
    "MarkdownExtractor",
    "PdfExtractor",
    "XlsxExtractor",
]