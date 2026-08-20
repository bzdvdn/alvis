"""Source adapters (filesystem, Confluence, GitHub, GitLab, S3, static URLs...)."""

from alvis.sources.base import Source, SourceError
from alvis.sources.confluence import ConfluenceSource
from alvis.sources.fs import FilesystemSource
from alvis.sources.github import GitHubSource
from alvis.sources.gitlab import GitLabSource
from alvis.sources.s3 import S3Source
from alvis.sources.url import StaticUrlSource

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