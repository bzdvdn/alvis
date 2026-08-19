from __future__ import annotations

from pathlib import Path

import pytest

from winnow.errors import PipelineError
from winnow.pipeline.engine import PipelineEngine
from winnow.sources import FilesystemSource, SourceError


async def test_pipeline_end_to_end(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    doc = docs / "guide.md"
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
        f"      path: {docs}\n"
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


async def test_pipeline_source_failure_wrapped(tmp_path: Path) -> None:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {tmp_path / 'missing'}\n"
        "  index:\n"
        "    type: memory\n",
        encoding="utf-8",
    )
    engine = PipelineEngine.from_yaml(config)
    with pytest.raises(PipelineError, match="source 'fs' failed"):
        await engine.run()


async def test_engine_describe() -> None:
    config = """\
pipeline:
  source:
    type: fs
    config:
      path: /tmp
  index:
    type: memory
"""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write(config)
        path = fh.name
    engine = PipelineEngine.from_yaml(path)
    description = engine.describe()
    assert "source: fs" in description
    assert "index: memory" in description


async def test_engine_describe_graph(tmp_path: Path) -> None:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {tmp_path}\n"
        "  chunk:\n"
        "    strategy: size\n"
        "    config:\n"
        "      max_chars: 80\n"
        "  embed:\n"
        "    type: openai\n"
        "    config:\n"
        "      model: text-embedding-3-small\n"
        "  index:\n"
        "    type: qdrant\n"
        "    config:\n"
        "      collection: winnow_docs\n",
        encoding="utf-8",
    )
    engine = PipelineEngine.from_yaml(config)
    graph = engine.describe_graph()
    assert "source fs" in graph
    assert "extract auto" in graph
    assert "chunk size" in graph
    assert "embed openai:text-embedding-3-small" in graph
    assert "index qdrant" in graph
    assert "▼" in graph


async def test_engine_embed_cache_stats(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(
        "# Guide\n\nSome body text for the cache test.\n", encoding="utf-8"
    )
    cache_path = tmp_path / "embeds.cache"
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {docs}\n"
        "  chunk:\n"
        "    config:\n"
        "      max_tokens: 20\n"
        "  embed:\n"
        "    type: default\n"
        "    config:\n"
        "      cache:\n"
        f"        path: {cache_path}\n"
        "  index:\n"
        "    type: memory\n",
        encoding="utf-8",
    )

    first = await PipelineEngine.from_yaml(config).run()
    assert first.embed_cache_misses == first.chunks_indexed
    assert first.embed_cache_hits == 0

    second = await PipelineEngine.from_yaml(config).run()
    assert second.embed_cache_hits == second.chunks_indexed
    assert second.embed_cache_misses == 0