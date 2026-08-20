from __future__ import annotations

from pathlib import Path

import pytest

from alvis.pipeline.engine import PipelineResult
from alvis.pipeline.runner import run, run_async, run_many, run_many_async


def _fs_pipeline(tmp_path: Path, name: str, text: str) -> Path:
    docs = tmp_path / name
    docs.mkdir()
    (docs / "doc.md").write_text(text, encoding="utf-8")
    config = tmp_path / f"{name}.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {docs}\n"
        "  index:\n"
        "    type: memory\n",
        encoding="utf-8",
    )
    return config


async def test_run_async_single(tmp_path: Path) -> None:
    config = _fs_pipeline(tmp_path, "one", "# Doc one\n\ntext\n")
    result = await run_async(config)
    assert isinstance(result, PipelineResult)
    assert result.documents_ingested == 1
    assert result.chunks_indexed >= 1


def test_run_sync_wrapper(tmp_path: Path) -> None:
    config = _fs_pipeline(tmp_path, "two", "# Doc two\n\ntext\n")
    result = run(str(config))
    assert result.documents_ingested == 1


async def test_run_many_async_parallel(tmp_path: Path) -> None:
    configs = [
        _fs_pipeline(tmp_path, "a", "# A\n\nbody a\n"),
        _fs_pipeline(tmp_path, "b", "# B\n\nbody b\n"),
        _fs_pipeline(tmp_path, "c", "# C\n\nbody c\n"),
    ]
    results = await run_many_async(configs, max_parallel=2)
    assert len(results) == 3
    assert all(r.documents_ingested == 1 for r in results)
    assert all(r.chunks_indexed >= 1 for r in results)


def test_run_many_sync(tmp_path: Path) -> None:
    configs = [
        _fs_pipeline(tmp_path, "x", "# X\n\nx\n"),
        _fs_pipeline(tmp_path, "y", "# Y\n\ny\n"),
    ]
    results = run_many(configs)
    assert [r.documents_ingested for r in results] == [1, 1]


async def test_run_many_accepts_loaded_config(tmp_path: Path) -> None:
    from alvis.config import load_config

    config = _fs_pipeline(tmp_path, "loaded", "# Loaded\n\nbody\n")
    pipeline = load_config(config)
    results = await run_many_async([pipeline])
    assert results[0].documents_ingested == 1


async def test_run_many_failure_propagates(tmp_path: Path) -> None:
    from alvis.config import ConfigError

    config = tmp_path / "missing.yaml"
    with pytest.raises(ConfigError):
        await run_many_async([config])


async def test_importable_from_package() -> None:
    import alvis

    assert callable(alvis.run)
    assert callable(alvis.run_many)