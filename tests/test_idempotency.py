from __future__ import annotations

from pathlib import Path

from alvis.config import load_config
from alvis.index import MemoryIndex
from alvis.pipeline.engine import PipelineEngine

_ORIGINAL = "# Guide\n\nIntroduction here.\n\n## Part 1\n\nBody of part one.\n"
_CHANGED = "# Guide\n\nIntroduction here.\n\n## Part 1\n\nCompletely new body.\n"


def _config(tmp_path: Path) -> str:
    docs = tmp_path / "docs"
    return (
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {docs}\n"
        "  chunk:\n"
        "    config:\n"
        "      max_tokens: 50\n"
        "  index:\n"
        "    type: memory\n"
    )


def _docs(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    return docs


async def test_rerun_is_idempotent(tmp_path: Path) -> None:
    doc = _docs(tmp_path) / "guide.md"
    doc.write_text(_ORIGINAL, encoding="utf-8")
    config = tmp_path / "p.yaml"
    config.write_text(_config(tmp_path), encoding="utf-8")
    index = MemoryIndex()
    engine = PipelineEngine(load_config(config), indexer=index)

    first = await engine.run()
    assert first.chunks_indexed >= 1

    second = await engine.run()
    assert second.chunks_indexed == first.chunks_indexed
    assert index.count == first.chunks_indexed


async def test_changed_document_replaces_not_duplicates(tmp_path: Path) -> None:
    doc = _docs(tmp_path) / "guide.md"
    doc.write_text(_ORIGINAL, encoding="utf-8")
    config = tmp_path / "p.yaml"
    config.write_text(_config(tmp_path), encoding="utf-8")
    index = MemoryIndex()
    engine = PipelineEngine(load_config(config), indexer=index)

    await engine.run()
    first_texts = {p.chunk.text for p in index.points.values()}

    doc.write_text(_CHANGED, encoding="utf-8")
    await engine.run()

    after_texts = {p.chunk.text for p in index.points.values()}
    assert any("Completely new body" in t for t in after_texts)
    assert any("Body of part one" in t for t in first_texts)
    assert not any("Body of part one" in t for t in after_texts)


async def test_deleted_document_is_pruned(tmp_path: Path) -> None:
    doc = _docs(tmp_path) / "guide.md"
    doc.write_text(_ORIGINAL, encoding="utf-8")
    config = tmp_path / "p.yaml"
    config.write_text(_config(tmp_path), encoding="utf-8")
    index = MemoryIndex()
    engine = PipelineEngine(load_config(config), indexer=index)

    await engine.run()
    assert index.count > 0

    doc.unlink()
    await engine.run()
    assert index.count == 0