"""Source adapters (filesystem, Confluence, GitHub, GitLab, S3, static URLs...)."""

from winnow.sources.base import Source, SourceError
from winnow.sources.confluence import ConfluenceSource
from winnow.sources.fs import FilesystemSource
from winnow.sources.github import GitHubSource
from winnow.sources.gitlab import GitLabSource
from winnow.sources.s3 import S3Source
from winnow.sources.url import StaticUrlSource

__all__ = [
    "ConfluenceSource",
    "FilesystemSource",
    "GitHubSource",
    "GitLabSource",
    "S3Source",
    "Source",
    "SourceError",
    "StaticUrlSource",
]