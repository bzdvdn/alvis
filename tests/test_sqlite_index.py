from __future__ import annotations

from pathlib import Path

import pytest

from alvis import dsl
from alvis.core.models import Chunk
from alvis.index.sqlite import SqliteIndex
from alvis.pipeline.runner import query_async, run_async


async def _upsert(
    index: SqliteIndex,
    text: str = "hello",
    uri: str = "u",
    vector: list[float] | None = None,
    source_id: str = "fs:.",
    hash_value: str = "h1",
) -> None:
    await index.upsert(
        Chunk(text=text, source_uri=uri, metadata={"heading": "H"}),
        vector or [0.5, 0.5],
        source_id=source_id,
        artifact_hash=hash_value,
    )


async def test_sqlite_upsert_and_search(tmp_path) -> None:
    index = SqliteIndex(str(tmp_path / "db.sqlite"))
    await _upsert(index, text="corporate knowledge", uri="u/a", vector=[1.0, 0.0])
    await _upsert(index, text="unrelated", uri="u/b", vector=[0.0, 1.0])

    hits = await index.search([1.0, 0.0], top_k=1)
    assert hits[0].text == "corporate knowledge"
    assert hits[0].metadata == {"heading": "H"}
    assert hits[0].score == pytest.approx(1.0)
    assert await index.count() == 2


async def test_sqlite_upsert_overwrites_identical_content(tmp_path) -> None:
    index = SqliteIndex(str(tmp_path / "db.sqlite"))
    await _upsert(index, text="same", uri="u", vector=[1.0, 0.0])
    await _upsert(index, text="same", uri="u", vector=[1.0, 0.0], hash_value="h2")
    assert await index.count() == 1


async def test_sqlite_reconcile_prunes_stale_points(tmp_path) -> None:
    index = SqliteIndex(str(tmp_path / "db.sqlite"))
    await _upsert(index, text="a", uri="u/a", vector=[1.0, 0.0], hash_value="new")
    await _upsert(index, text="b", uri="u/b", vector=[0.0, 1.0], hash_value="old")

    # 'u/b' is stale: removed from current set entirely -> pruned
    await index.reconcile("fs:.", {"u/a": "new"})
    hits = await index.search([1.0, 0.0], top_k=10)
    assert [hit.source_uri for hit in hits] == ["u/a"]
    assert await index.count() == 1


async def test_sqlite_reconcile_removes_changed_document(tmp_path) -> None:
    index = SqliteIndex(str(tmp_path / "db.sqlite"))
    await _upsert(index, text="old body", uri="u/a", vector=[1.0, 0.0], hash_value="old")
    await index.reconcile("fs:.", {"u/a": "new"})

    hits = await index.search([1.0, 0.0], top_k=10)
    assert hits == []
    assert await index.count() == 0


async def test_sqlite_persists_across_instances(tmp_path) -> None:
    db = tmp_path / "db.sqlite"
    first = SqliteIndex(str(db))
    await _upsert(first, text="persisted", uri="u", vector=[1.0, 0.0])

    # A fresh index over the same file sees the earlier points.
    second = SqliteIndex(str(db))
    assert await second.count() == 1
    hits = await second.search([1.0, 0.0], top_k=1)
    assert hits[0].text == "persisted"


async def test_sqlite_memory_path_is_ephemeral() -> None:
    index = SqliteIndex(":memory:")
    await _upsert(index, text="tmp", uri="u")
    assert await index.count() == 1
    other = SqliteIndex(":memory:")
    assert await other.count() == 0


async def test_sqlite_pipeline_persists_across_runs(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "guide.md").write_text(
        "Alvis stores corporate knowledge in a SQLite index.", encoding="utf-8"
    )
    db = tmp_path / "state.db"
    config = dsl.pipeline(
        dsl.fs(str(corpus)),
        chunk=dsl.chunk(max_tokens=20, overlap=0),
        index=dsl.sqlite(path=str(db)),
    )

    await run_async(config)
    hits = await query_async(config, "corporate knowledge", top_k=1)
    assert hits and "corporate knowledge" in hits[0].text

    # A fresh process/config over the same database file still finds the chunk.
    hits_again = await query_async(config, "corporate knowledge", top_k=1)
    assert hits_again and "corporate knowledge" in hits_again[0].text
