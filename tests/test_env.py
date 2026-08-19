"""Environment helpers — `.env` loading and early secret validation."""

from __future__ import annotations

import os
from pathlib import Path

from winnow.config import PipelineConfig
from winnow.env import load_dotenv, missing_env


def test_load_dotenv_parses_pairs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("WINNOW_TEST_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# comment\n'
        'WINNOW_TEST_KEY="hello world"\n'
        'WINNOW_EMPTY=\n'
        'WINNOW_NOPE_NOT_A_PAIR\n'
        '  \n',
        encoding="utf-8",
    )
    loaded = load_dotenv(env_file)
    assert loaded == {"WINNOW_TEST_KEY": "hello world", "WINNOW_EMPTY": ""}
    assert os.environ["WINNOW_TEST_KEY"] == "hello world"


def test_load_dotenv_does_not_overwrite(monkeypatch) -> None:
    monkeypatch.setenv("WINNOW_KEEP", "existing")
    env_file = Path("does-not-matter")
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, encoding="utf-8": "WINNOW_KEEP=new\n",
    )
    assert load_dotenv(env_file) == {}
    assert os.environ["WINNOW_KEEP"] == "existing"


def test_load_dotenv_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_dotenv(tmp_path / "missing.env") == {}


def _config(**changes: object) -> PipelineConfig:
    base = {
        "source": {"type": "confluence"},
        "index": {"type": "qdrant"},
    }
    base.update(changes)
    return PipelineConfig.model_validate(base)


def test_missing_env_reports_absent_names(monkeypatch) -> None:
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    pipeline = _config(
        source={
            "type": "confluence",
            "config": {
                "url": "http://x",
                "api_token_env": "CONFLUENCE_API_TOKEN",
                "username": "bot",
            },
        }
    )
    assert missing_env(pipeline) == ["CONFLUENCE_API_TOKEN"]


def test_missing_env_treats_empty_value_as_missing(monkeypatch) -> None:
    monkeypatch.setenv("QDRANT_API_KEY", "")
    pipeline = _config(
        source={
            "type": "confluence",
            "config": {"api_token_env": "QDRANT_API_KEY"},
        }
    )
    assert missing_env(pipeline) == ["QDRANT_API_KEY"]


def test_missing_env_empty_when_satisfied(monkeypatch) -> None:
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "secret")
    pipeline = _config(
        source={
            "type": "confluence",
            "config": {
                "url": "http://x",
                "api_token_env": "CONFLUENCE_API_TOKEN",
            },
        }
    )
    assert missing_env(pipeline) == []


def test_missing_env_sorted_unique(monkeypatch) -> None:
    monkeypatch.delenv("AAA", raising=False)
    monkeypatch.delenv("BBB", raising=False)
    pipeline = _config(
        source={
            "type": "confluence",
            "config": {"foo_env": "BBB", "bar_env": "AAA", "baz_env": "AAA"},
        }
    )
    assert missing_env(pipeline) == ["AAA", "BBB"]