"""Source adapters (filesystem, Confluence, GitHub, GitLab, S3...)."""

from winnow.sources.base import Source, SourceError
from winnow.sources.confluence import ConfluenceSource
from winnow.sources.fs import FilesystemSource
from winnow.sources.github import GitHubSource
from winnow.sources.gitlab import GitLabSource
from winnow.sources.s3 import S3Source

__all__ = [
    "ConfluenceSource",
    "FilesystemSource",
    "GitHubSource",
    "GitLabSource",
    "S3Source",
    "Source",
    "SourceError",
]