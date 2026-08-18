"""Content-type detection for source files."""

from __future__ import annotations

from pathlib import Path

_MARKDOWN = {".md", ".markdown"}
_HTML = {".html", ".htm"}
_TEXT = {".txt", ".rst", ".csv", ".json", ".toml", ".yaml", ".yml", ".xml"}

SOURCE_CODE = {
    ".py",
    ".go",
    ".java",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".rs",
    ".rb",
    ".php",
    ".sh",
    ".sql",
    ".m",
    ".scala",
    ".kt",
    ".cs",
    ".dart",
    ".swift",
    ".lua",
    ".pl",
    ".r",
}

TEXT_EXTENSIONS = _MARKDOWN | _HTML | _TEXT | SOURCE_CODE

_BINARY_DOCUMENTS = {".pdf", ".docx", ".xlsx"}

INGESTIBLE_EXTENSIONS = TEXT_EXTENSIONS | _BINARY_DOCUMENTS

_MIMETYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
}


def content_type(path: str | Path) -> str:
    """Map a file path to its Winnow content type."""
    suffix = Path(path).suffix.lower()
    if suffix in _MARKDOWN:
        return "text/markdown"
    if suffix in _HTML:
        return "text/html"
    if suffix in _MIMETYPES:
        return _MIMETYPES[suffix]
    return "text/plain"


def is_text_file(path: str | Path) -> bool:
    """True for extensions with textual content."""
    return Path(path).suffix.lower() in TEXT_EXTENSIONS


def is_ingestible(path: str | Path) -> bool:
    """True for extensions the extract stage understands (text + PDF/DOCX/XLSX)."""
    return Path(path).suffix.lower() in INGESTIBLE_EXTENSIONS


def matches_globs(globs: list[str] | None, rel_path: str) -> bool:
    """True when ``rel_path`` matches any of the ``fnmatch`` patterns."""
    if not globs:
        return False
    import fnmatch

    return any(fnmatch.fnmatch(rel_path, pattern) for pattern in globs)