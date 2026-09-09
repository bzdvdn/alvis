from __future__ import annotations

from pathlib import Path

import pytest

from alvis.config import load_config
from alvis.config.loader import ConfigError

VALID_PIPELINE = """\
pipeline:
  source:
    type: confluence
    config:
      url: https://wiki.example.com

  extract:
    strategy: auto

  chunk:
    strategy: auto
    config:
      max_tokens: 500
      overlap: 50

  embed:
    type: default

  index:
    type: qdrant
"""


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "pipeline.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_valid_config(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path, VALID_PIPELINE))
    assert cfg.source.type == "confluence"
    assert cfg.extract.strategy == "auto"
    assert cfg.chunk.max_tokens == 500
    assert cfg.chunk.overlap == 50
    assert cfg.embed.type == "default"
    assert cfg.index.type == "qdrant"


def test_minimal_config_supplies_defaults(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path, "pipeline:\n  source:\n    type: confluence\n"))
    assert cfg.extract.strategy == "auto"
    assert cfg.chunk.strategy == "auto"
    assert cfg.chunk.max_tokens == 500
    assert cfg.chunk.overlap == 50
    assert cfg.embed.type == "default"
    assert cfg.index is None


def test_missing_pipeline_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing top-level 'pipeline'"):
        load_config(write(tmp_path, "foo: bar\n"))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(write(tmp_path, "pipeline: [unclosed\n"))


def test_invalid_chunk_config_rejected(tmp_path: Path) -> None:
    content = (
        "pipeline:\n"
        "  source:\n"
        "    type: confluence\n"
        "  chunk:\n"
        "    strategy: auto\n"
        "    config:\n"
        "      max_tokens: 500\n"
        "      overlap: -1\n"
    )
    with pytest.raises(ConfigError, match="overlap"):
        load_config(write(tmp_path, content))


def test_unknown_stage_key_rejected(tmp_path: Path) -> None:
    content = "pipeline:\n  source:\n    type: confluence\n  bogus: stage\n"
    with pytest.raises(ConfigError, match="bogus"):
        load_config(write(tmp_path, content))


def test_source_acl_accepts_list_of_strings(tmp_path: Path) -> None:
    content = (
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        "      path: .\n"
        "      acl: [eng, ops]\n"
    )
    config = load_config(write(tmp_path, content))
    assert config.source.config["acl"] == ["eng", "ops"]


def test_source_acl_rejects_non_list(tmp_path: Path) -> None:
    content = (
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        "      path: .\n"
        "      acl: eng\n"
    )
    with pytest.raises(ConfigError, match="acl"):
        load_config(write(tmp_path, content))


def test_source_acl_rejects_non_string_items(tmp_path: Path) -> None:
    content = (
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        "      path: .\n"
        "      acl: [eng, 42]\n"
    )
    with pytest.raises(ConfigError, match="acl"):
        load_config(write(tmp_path, content))


def test_schema_version_defaults_to_one(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path, VALID_PIPELINE))
    assert cfg.schema_version == 1


def test_explicit_schema_version_one_accepted(tmp_path: Path) -> None:
    content = "pipeline:\n  schema_version: 1\n  source:\n    type: confluence\n"
    cfg = load_config(write(tmp_path, content))
    assert cfg.schema_version == 1


def test_unsupported_schema_version_rejected(tmp_path: Path) -> None:
    content = "pipeline:\n  schema_version: 2\n  source:\n    type: confluence\n"
    with pytest.raises(ConfigError, match="schema_version"):
        load_config(write(tmp_path, content))


def test_cct_schema_version_on_document() -> None:
    from alvis.config.models import PIPELINE_SCHEMA_VERSION
    from alvis.core.models import CCT_SCHEMA_VERSION, Document

    assert PIPELINE_SCHEMA_VERSION == 1
    assert CCT_SCHEMA_VERSION == 1
    document = Document(uri="u", title="t")
    assert document.schema_version == 1

    loaded = Document.model_validate(document.model_dump())
    assert loaded == document
    assert loaded.schema_version == 1


def test_embed_cache_bool_and_path(tmp_path: Path) -> None:
    cfg = load_config(
        write(
            tmp_path,
            "pipeline:\n"
            "  source:\n    type: confluence\n"
            "  embed:\n    type: openai\n"
            "    config:\n      model: m\n      cache: true\n",
        )
    )
    assert cfg.embed.cache_enabled is True
    assert cfg.embed.cache_path is None

    cfg = load_config(
        write(
            tmp_path,
            "pipeline:\n"
            "  source:\n    type: confluence\n"
            "  embed:\n    type: openai\n"
            "    config:\n      model: m\n      cache:\n        path: .alvis/cache\n",
        )
    )
    assert cfg.embed.cache_enabled is True
    assert cfg.embed.cache_path == ".alvis/cache"


def test_embed_cache_disabled_by_default_and_explicit_false(tmp_path: Path) -> None:
    cfg = load_config(
        write(tmp_path, "pipeline:\n  source:\n    type: confluence\n")
    )
    assert cfg.embed.cache_enabled is False
    assert cfg.embed.cache_path is None


def test_embed_cache_invalid_shape_rejected(tmp_path: Path) -> None:
    content = (
        "pipeline:\n"
        "  source:\n    type: confluence\n"
        "  embed:\n    type: openai\n"
        "    config:\n      model: m\n      cache: [1, 2]\n"
    )
    with pytest.raises(ConfigError, match="cache"):
        load_config(write(tmp_path, content))

    content = (
        "pipeline:\n"
        "  source:\n    type: confluence\n"
        "  embed:\n    type: openai\n"
        "    config:\n      model: m\n      cache:\n        path: 42\n"
    )
    with pytest.raises(ConfigError, match="cache.path"):
        load_config(write(tmp_path, content))