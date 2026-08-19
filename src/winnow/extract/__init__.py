"""Extraction stage — artifacts → canonical documents."""

from winnow.extract.base import AutoExtractor, Extractor
from winnow.extract.csv import CsvExtractor
from winnow.extract.documents import DocxExtractor, PdfExtractor, XlsxExtractor
from winnow.extract.json import JsonExtractor
from winnow.extract.markdown import MarkdownExtractor

__all__ = [
    "AutoExtractor",
    "CsvExtractor",
    "DocxExtractor",
    "Extractor",
    "JsonExtractor",
    "MarkdownExtractor",
    "PdfExtractor",
    "XlsxExtractor",
]