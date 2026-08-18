"""Source adapters (Confluence, GitLab, S3...)."""

from winnow.sources.base import Source, SourceError
from winnow.sources.confluence import ConfluenceSource
from winnow.sources.fs import FilesystemSource

__all__ = [
    "ConfluenceSource",
    "FilesystemSource",
    "Source",
    "SourceError",
]