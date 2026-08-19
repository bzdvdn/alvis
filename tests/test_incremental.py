"""Incremental ingestion — DocStore state and skip-on-unchanged behaviour."""

from __future__ import annotations

from pathlib import Path

from winnow import dsl, run_async
from winnow.docstore import DocEntry, DocStore
from winnow.index import MemoryIndex
from winnow.pipeline.engine import PipelineResult, pipeline_signature


def _write(corpus: Path, name: str, body: str) -> None:
    (corpus / name).write_text(body, encoding="utf-8")


async def _run(
    corpus: Path,
    state: Path,
    *,
    max_tokens: int = 500,
    indexer: MemoryIndex | None = None,
) -> tuple[MemoryIndex, PipelineResult]:
    if indexer is None:
        indexer = MemoryIndex()
    cfg = dsl.pipeline(
        dsl.fs(path=str(corpus)),
        chunk=dsl.chunk(max_tokens=max_tokens),
        index=dsl.memory(),
    )
    result = await run_async(
        cfg,
        indexer=indexer,
        incremental=True,
        state_path=str(state),
    )
    return indexer, result


def test_docstore_roundtrip(tmp_path: Path) -> None:
    store = DocStore(tmp_path / "state.json")
    assert store.entry("fs:/x", "sig-1", "a.md") is None

    assert (
        store.commit(
            "fs:/x",
            "sig-1",
            {"a.md": DocEntry("h1", "e1"), "b.md": DocEntry("h2")},
        )
        == 0
    )
    store.save()

    reloaded = DocStore(tmp_path / "state.json")
    assert reloaded.entry("fs:/x", "sig-1", "a.md") == DocEntry("h1", "e1")
    assert reloaded.entry("fs:/x", "sig-1", "c.md") is None
    assert reloaded.commit("fs:/x", "sig-2", {"a.md": DocEntry("h1")}) == 1


def test_docstore_fingerprint_is_signature_scoped() -> None:
    store = DocStore(Path("does-not-exist.json"))
    store.commit("s1", "sig-a", {"u": DocEntry("h")})
    assert store.entry("s1", "sig-a", "u") == DocEntry("h")
    assert store.entry("s1", "sig-b", "u") is None
    assert store.entry("s2", "sig-a", "u") is None


def test_docstore_migrates_legacy_content_only_entries(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text(
        '{"version": 1, "sources": {"s1": '
        '{"signature": "sig-a", "documents": {"u": "hash1"}}}}',
        encoding="utf-8",
    )
    store = DocStore(state)
    entry = store.entry("s1", "sig-a", "u")
    assert entry is not None
    assert entry.content == "hash1"
    assert entry.listing is None


def test_pipeline_signature_distinguishes_chunk_settings() -> None:
    base = dsl.pipeline(dsl.fs(path="/x"), chunk=dsl.chunk(max_tokens=500))
    other = dsl.pipeline(dsl.fs(path="/x"), chunk=dsl.chunk(max_tokens=80))
    assert pipeline_signature(base) != pipeline_signature(other)


async def test_incremental_skips_unchanged(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write(corpus, "a.md", "# A\nfirst draft")
    _write(corpus, "b.md", "# B\nsecond doc")
    state = tmp_path / "state.json"

    indexer, first = await _run(corpus, state)
    assert first.documents_ingested == 2
    assert first.documents_changed == 2
    assert first.documents_skipped == 0

    indexer, second = await _run(corpus, state, indexer=indexer)
    assert second.documents_changed == 0
    assert second.documents_skipped == 2
    assert second.chunks_indexed == 0
    assert indexer.count == first.chunks_indexed

    assert state.exists()


async def test_incremental_reprocesses_changed_and_prunes_deleted(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write(corpus, "a.md", "# A\nfirst draft")
    _write(corpus, "b.md", "# B\nsecond doc")
    state = tmp_path / "state.json"

    indexer, first = await _run(corpus, state)
    assert first.documents_deleted == 0

    _write(corpus, "a.md", "# A\nrewritten")
    (corpus / "b.md").unlink()

    indexer, second = await _run(corpus, state, indexer=indexer)
    assert second.documents_changed == 1
    assert second.documents_skipped == 0
    assert second.documents_deleted == 1

    hits = await indexer.search([1.0] * 768, top_k=10)
    texts = " ".join(hit.text for hit in hits)
    assert "rewritten" in texts
    assert "second doc" not in texts


async def test_incremental_invalidated_by_chunk_config(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write(corpus, "a.md", "# A\nfirst draft\n\nmore words\n\nstill more\n\ntail")
    state = tmp_path / "state.json"

    indexer, first = await _run(corpus, state, max_tokens=500)
    assert first.documents_changed == 1

    indexer, second = await _run(corpus, state, max_tokens=80, indexer=indexer)
    assert second.documents_changed == 1
    assert second.documents_skipped == 0