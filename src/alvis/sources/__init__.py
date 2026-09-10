"""Source adapters (filesystem, Confluence, GitHub, GitLab, S3, Notion, Jira,
SharePoint, Google Drive, static URLs...)."""

from alvis.sources.base import Source, SourceError
from alvis.sources.confluence import ConfluenceSource
from alvis.sources.fs import FilesystemSource
from alvis.sources.gdrive import GoogleDriveSource
from alvis.sources.github import GitHubSource
from alvis.sources.gitlab import GitLabSource
from alvis.sources.jira import JiraSource
from alvis.sources.notion import NotionSource
from alvis.sources.s3 import S3Source
from alvis.sources.sharepoint import SharePointSource
from alvis.sources.url import StaticUrlSource

__all__ = [
    "ConfluenceSource",
    "FilesystemSource",
    "GitHubSource",
    "GitLabSource",
    "GoogleDriveSource",
    "JiraSource",
    "NotionSource",
    "S3Source",
    "SharePointSource",
    "Source",
    "SourceError",
    "StaticUrlSource",
]