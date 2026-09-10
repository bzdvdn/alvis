from __future__ import annotations

from pathlib import Path

from alvis import dsl
from alvis.config import load_config
from alvis.pipeline.runner import run_async


def test_fs_dsl_matches_yaml(tmp_path: Path) -> None:
    cfg = dsl.pipeline(dsl.fs(str(tmp_path)), index=dsl.memory())
    yaml_path = tmp_path / "p.yaml"
    yaml_path.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {tmp_path}\n"
        "  index:\n"
        "    type: memory\n",
        encoding="utf-8",
    )
    assert cfg == load_config(yaml_path)


def test_fs_dsl_acl_matches_yaml(tmp_path: Path) -> None:
    cfg = dsl.pipeline(dsl.fs(str(tmp_path), acl=["eng"]), index=dsl.memory())
    yaml_path = tmp_path / "p.yaml"
    yaml_path.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {tmp_path}\n"
        "      acl: [eng]\n"
        "  index:\n"
        "    type: memory\n",
        encoding="utf-8",
    )
    assert cfg == load_config(yaml_path)
    assert cfg.source.config["acl"] == ["eng"]


def test_fs_dsl_omits_acl_when_not_given(tmp_path: Path) -> None:
    cfg = dsl.fs(str(tmp_path))
    assert "acl" not in cfg.config


def test_s3_dsl_populates_config() -> None:
    cfg = dsl.s3(
        url="http://localhost:9000",
        bucket="alvis",
        access_key_env="A",
        secret_key_env="B",
        prefix="docs",
        exclude_globs=["**/*.mp4"],
        region="eu-central-1",
    )
    assert cfg.type == "s3"
    assert cfg.config["bucket"] == "alvis"
    assert cfg.config["prefix"] == "docs"
    assert cfg.config["exclude_globs"] == ["**/*.mp4"]
    assert cfg.config["region"] == "eu-central-1"


def test_self_hosted_gitlab_keeps_url() -> None:
    cfg = dsl.gitlab(project="grp/proj", url="https://git.example.com")
    assert cfg.config["url"] == "https://git.example.com"


def test_gitlab_group_config_roundtrips() -> None:
    cfg = dsl.gitlab(
        group="grp",
        project_include_globs=["grp/docs-*"],
        project_exclude_globs=["grp/archive-*"],
        include_archived=True,
    )
    assert cfg.config["group"] == "grp"
    assert cfg.config["project_include_globs"] == ["grp/docs-*"]
    assert cfg.config["project_exclude_globs"] == ["grp/archive-*"]
    assert cfg.config["include_archived"] is True
    assert "project" not in cfg.config


def test_gitlab_requires_project_or_group() -> None:
    import pytest

    with pytest.raises(ValueError):
        dsl.gitlab()


def test_defaults_are_omitted_from_config() -> None:
    cfg = dsl.s3(url="u", bucket="b", access_key_env="A", secret_key_env="S")
    assert "region" not in cfg.config
    assert "retries" not in cfg.config

    gh = dsl.github(repo="acme/kb")
    assert "branch" not in gh.config

    ch = dsl.chunk()
    assert ch.max_tokens == 500
    assert ch.overlap == 50


def test_sections_chunk_strategy_roundtrips() -> None:
    cfg = dsl.pipeline(
        dsl.fs("."),
        chunk=dsl.chunk(strategy="sections", max_tokens=120),
        index=dsl.memory(),
    )
    assert cfg.chunk.strategy == "sections"
    assert cfg.chunk.max_tokens == 120


def test_size_chunk_strategy_roundtrips() -> None:
    cfg = dsl.chunk(strategy="size", max_chars=2048, overlap_chars=64)
    assert cfg.strategy == "size"
    assert cfg.max_chars == 2048
    assert cfg.overlap_chars == 64
    assert cfg.max_tokens == 500


def test_pgvector_dsl_populates_config() -> None:
    cfg = dsl.pgvector(dsn="postgresql://alvis@localhost/alvis", table="chunks")
    assert cfg.type == "pgvector"
    assert cfg.config["dsn"] == "postgresql://alvis@localhost/alvis"
    assert cfg.config["table"] == "chunks"
    assert "dsn_env" not in cfg.config


def test_elasticsearch_dsl_populates_config() -> None:
    cfg = dsl.elasticsearch(
        url="http://localhost:9200", index="alvis_docs", username="elastic"
    )
    assert cfg.type == "elasticsearch"
    assert cfg.config["url"] == "http://localhost:9200"
    assert cfg.config["index"] == "alvis_docs"
    assert cfg.config["username"] == "elastic"
    assert "api_token_env" not in cfg.config
    assert "retries" not in cfg.config


def test_sqlite_dsl_populates_config() -> None:
    cfg = dsl.sqlite(path="local.db")
    assert cfg.type == "sqlite"
    assert cfg.config["path"] == "local.db"

    default = dsl.sqlite()
    assert default.config["path"] == "alvis.db"


async def test_dsl_pipeline_runs(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\n\nBody here.\n", encoding="utf-8")

    cfg = dsl.pipeline(dsl.fs(str(docs)), chunk=dsl.chunk(max_tokens=30), index=dsl.memory())
    result = await run_async(cfg)
    assert result.documents_ingested == 1
    assert result.chunks_indexed >= 1


async def test_pipeline_assemble_overrides() -> None:
    cfg = dsl.pipeline(
        dsl.fs("/tmp"),
        chunk=dsl.chunk(max_tokens=10),
        index=dsl.qdrant(url="http://localhost:6333", collection="c"),
    )
    assert cfg.chunk.max_tokens == 10
    assert cfg.index is not None
    assert cfg.index.type == "qdrant"
    assert cfg.embed.type == "default"


def test_fs_dsl_max_bytes_roundtrips() -> None:
    cfg = dsl.fs("/tmp", max_bytes=1024)
    assert cfg.config["max_bytes"] == 1024
    assert "max_bytes" not in dsl.fs("/tmp").config


def test_github_dsl_max_bytes_and_max_concurrency_roundtrip() -> None:
    cfg = dsl.github(repo="acme/kb", max_bytes=2048, max_concurrency=3)
    assert cfg.config["max_bytes"] == 2048
    assert cfg.config["max_concurrency"] == 3

    default = dsl.github(repo="acme/kb")
    assert "max_bytes" not in default.config
    assert "max_concurrency" not in default.config


def test_gitlab_dsl_max_bytes_and_max_concurrency_roundtrip() -> None:
    cfg = dsl.gitlab(project="grp/proj", max_bytes=2048, max_concurrency=3)
    assert cfg.config["max_bytes"] == 2048
    assert cfg.config["max_concurrency"] == 3

    default = dsl.gitlab(project="grp/proj")
    assert "max_bytes" not in default.config
    assert "max_concurrency" not in default.config


def test_s3_dsl_max_bytes_and_max_concurrency_roundtrip() -> None:
    cfg = dsl.s3(
        url="u", bucket="b", access_key_env="A", secret_key_env="S",
        max_bytes=2048, max_concurrency=3,
    )
    assert cfg.config["max_bytes"] == 2048
    assert cfg.config["max_concurrency"] == 3

    default = dsl.s3(url="u", bucket="b", access_key_env="A", secret_key_env="S")
    assert "max_bytes" not in default.config
    assert "max_concurrency" not in default.config


def test_static_url_dsl_max_concurrency_roundtrips() -> None:
    cfg = dsl.static_url(urls=["https://example.com/"], max_concurrency=3)
    assert cfg.config["max_concurrency"] == 3
    assert "max_concurrency" not in dsl.static_url(urls=["https://example.com/"]).config


def test_confluence_dsl_max_concurrency_roundtrips() -> None:
    cfg = dsl.confluence(url="https://wiki.example.com", space="TEAM", max_concurrency=3)
    assert cfg.config["max_concurrency"] == 3
    default = dsl.confluence(url="https://wiki.example.com", space="TEAM")
    assert "max_concurrency" not in default.config


def test_embed_openai_dsl_max_concurrency_roundtrips() -> None:
    cfg = dsl.embed_openai(base_url="https://api.openai.com/v1", model="m", max_concurrency=8)
    assert cfg.config["max_concurrency"] == 8
    default = dsl.embed_openai(base_url="https://api.openai.com/v1", model="m")
    assert "max_concurrency" not in default.config