"""factories.source_identity — every source type must produce a distinct
identity for distinct configs, or reconcile state collides and one
pipeline's run deletes another's points.

Regression coverage for a real bug found via production usage: `gitlab`
group sources (and, it turned out, every source type added after it —
notion/jira/sharepoint/gdrive) fell back to a constant identity whenever
the distinguishing config lived outside the one field each branch happened
to check, so two different GitLab groups (or two Jira projects, two
Notion workspaces, ...) sharing an index collided.
"""

from __future__ import annotations

from alvis.config.models import SourceConfig
from alvis.factories import source_identity


def test_gitlab_group_sources_get_distinct_identities() -> None:
    a = source_identity(SourceConfig(type="gitlab", config={"group": "team-a"}))
    b = source_identity(SourceConfig(type="gitlab", config={"group": "team-b"}))
    assert a != b


def test_gitlab_project_and_group_sources_get_distinct_identities() -> None:
    project = source_identity(SourceConfig(type="gitlab", config={"project": "a/b"}))
    group = source_identity(SourceConfig(type="gitlab", config={"group": "a/b"}))
    assert project != group


def test_gitlab_group_identity_includes_host() -> None:
    a = source_identity(
        SourceConfig(type="gitlab", config={"group": "team", "url": "https://a.example.com"})
    )
    b = source_identity(
        SourceConfig(type="gitlab", config={"group": "team", "url": "https://b.example.com"})
    )
    assert a != b


def test_notion_sources_get_distinct_identities_per_token_env() -> None:
    a = source_identity(SourceConfig(type="notion", config={"api_token_env": "NOTION_A"}))
    b = source_identity(SourceConfig(type="notion", config={"api_token_env": "NOTION_B"}))
    assert a != b


def test_jira_sources_get_distinct_identities_per_project() -> None:
    a = source_identity(
        SourceConfig(type="jira", config={"url": "https://x.atlassian.net", "project": "ENG"})
    )
    b = source_identity(
        SourceConfig(type="jira", config={"url": "https://x.atlassian.net", "project": "OPS"})
    )
    assert a != b


def test_jira_sources_get_distinct_identities_per_jql() -> None:
    a = source_identity(
        SourceConfig(type="jira", config={"url": "https://x.atlassian.net", "jql": "a"})
    )
    b = source_identity(
        SourceConfig(type="jira", config={"url": "https://x.atlassian.net", "jql": "b"})
    )
    assert a != b


def test_sharepoint_sources_get_distinct_identities_per_site() -> None:
    a = source_identity(
        SourceConfig(type="sharepoint", config={"site_url": "https://x.sharepoint.com/sites/A"})
    )
    b = source_identity(
        SourceConfig(type="sharepoint", config={"site_url": "https://x.sharepoint.com/sites/B"})
    )
    assert a != b


def test_gdrive_sources_get_distinct_identities_per_folder() -> None:
    a = source_identity(SourceConfig(type="gdrive", config={"folder_id": "f1"}))
    b = source_identity(SourceConfig(type="gdrive", config={"folder_id": "f2"}))
    assert a != b


def test_gdrive_sources_fall_back_to_key_env_when_no_folder_given() -> None:
    a = source_identity(SourceConfig(type="gdrive", config={"service_account_key_env": "A"}))
    b = source_identity(SourceConfig(type="gdrive", config={"service_account_key_env": "B"}))
    assert a != b
