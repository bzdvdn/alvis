from __future__ import annotations

from pathlib import Path

import pytest

from winnow.pipeline.engine import PipelineEngine
from winnow.sources import FilesystemSource, SourceError


async def test_pipeline_end_to_end(tmp_path: Path) -> None:
    doc = tmp_path / "guide.md"
    doc.write_text(
        "# Guide\n\nIntroduction here.\n\n## Part 1\n\nBody of part one.\n",
        encoding="utf-8",
    )
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {tmp_path}\n"
        "  chunk:\n"
        "    config:\n"
        "      max_tokens: 20\n"
        "  index:\n"
        "    type: memory\n",
        encoding="utf-8",
    )

    engine = PipelineEngine.from_yaml(config)
    result = await engine.run()

    assert result.documents_ingested == 1
    assert result.chunks_indexed >= 1


async def test_pipeline_fs_source_reads_docs(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A\n\ntext\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("plain text\n", encoding="utf-8")
    source = FilesystemSource(path=str(tmp_path))
    artifacts = await source.fetch()
    assert {a.step_id.split("/")[-1] for a in artifacts} == {"a.md", "b.txt"}
    assert {a.content_type for a in artifacts} == {"text/markdown", "text/plain"}


async def test_pipeline_fs_missing_path_raises(tmp_path: Path) -> None:
    source = FilesystemSource(path=str(tmp_path / "nope"))
    with pytest.raises(SourceError):
        await source.fetch()