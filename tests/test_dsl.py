from __future__ import annotations

from pathlib import Path

from winnow import dsl
from winnow.config import load_config
from winnow.pipeline.runner import run_async


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


def test_s3_dsl_populates_config() -> None:
    cfg = dsl.s3(
        url="http://localhost:9000",
        bucket="winnow",
        access_key_env="A",
        secret_key_env="B",
        prefix="docs",
        exclude_globs=["**/*.mp4"],
        region="eu-central-1",
    )
    assert cfg.type == "s3"
    assert cfg.config["bucket"] == "winnow"
    assert cfg.config["prefix"] == "docs"
    assert cfg.config["exclude_globs"] == ["**/*.mp4"]
    assert cfg.config["region"] == "eu-central-1"


def test_self_hosted_gitlab_keeps_url() -> None:
    cfg = dsl.gitlab(project="grp/proj", url="https://git.example.com")
    assert cfg.config["url"] == "https://git.example.com"


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


def test_pgvector_dsl_populates_config() -> None:
    cfg = dsl.pgvector(dsn="postgresql://winnow@localhost/winnow", table="chunks")
    assert cfg.type == "pgvector"
    assert cfg.config["dsn"] == "postgresql://winnow@localhost/winnow"
    assert cfg.config["table"] == "chunks"
    assert "dsn_env" not in cfg.config


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