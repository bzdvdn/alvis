"""PgVectorIndex behaviour (SQL generation & config validation).

A live PostgreSQL/pgvector server is not required: ``connect`` is swapped for
a recording fake, and the emitted SQL/parameters are asserted.
"""

from __future__ import annotations

from typing import Any

import pytest

from alvis.core.ids import point_id
from alvis.core.models import Chunk
from alvis.index import PgVectorIndex


class _Conn:
    """Fake psycopg async connection that records executed statements."""

    closed = False

    def __init__(self, index: PgVectorIndex) -> None:
        self._index = index
        self.statements: list[str] = []
        self.params: list[tuple[Any, ...]] = []

    async def compose(self, sql: Any) -> str:
        return sql.as_string(None)

    async def execute(self, sql: Any, params: tuple[Any, ...] | None = None) -> None:
        self.statements.append(sql.as_string(None))
        self.params.append(params or ())


def _swap(index: PgVectorIndex, conn: _Conn) -> None:
    async def connect() -> _Conn:
        return conn

    index._connect = connect  # type: ignore[method-assign]


def test_pgvector_rejects_missing_dsn() -> None:
    with pytest.raises(ValueError, match="dsn"):
        PgVectorIndex()


def test_pgvector_rejects_missing_env() -> None:
    with pytest.raises(ValueError, match="secret"):
        PgVectorIndex(dsn_env="DEFINITELY_NOT_SET_VAR_123")


def test_pgvector_dsn_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_ALVIS_DSN", "postgresql://u@h/db")
    assert PgVectorIndex(dsn_env="TEST_ALVIS_DSN").dsn == "postgresql://u@h/db"


def test_pgvector_rejects_both_dsn_and_env() -> None:
    with pytest.raises(ValueError, match="either dsn or dsn_env"):
        PgVectorIndex(dsn="x", dsn_env="TEST_ALVIS_DSN")


def test_pgvector_upsert_constructs_idempotent_insert() -> None:
    index = PgVectorIndex(dsn="postgresql://u@h/db")
    conn = _Conn(index)
    _swap(index, conn)

    chunk = Chunk(text="hello", source_uri="s3://a", metadata={"heading": "H"})

    async def run() -> None:
        await index.upsert(chunk, [0.1, 0.2], source_id="s3", artifact_hash="abc")

    import asyncio

    asyncio.run(run())

    assert conn.statements[0].startswith("CREATE EXTENSION IF NOT EXISTS vector")
    assert conn.statements[1].startswith("CREATE TABLE IF NOT EXISTS")
    assert "ON CONFLICT (id)" in conn.statements[-1]
    assert conn.params[-1] == (
        str(point_id(chunk.source_uri, chunk.text)),
        chunk.source_uri,
        chunk.text,
        "s3",
        "abc",
        "[0.1,0.2]",
        '{"heading": "H"}',
    )


def test_pgvector_reconcile_prunes_stale_hashes() -> None:
    index = PgVectorIndex(dsn="postgresql://u@h/db")
    conn = _Conn(index)
    _swap(index, conn)

    async def run() -> None:
        await index.reconcile(
            "s3",
            {"s3://a": "h1", "s3://b": "h2"},
        )

    import asyncio

    asyncio.run(run())

    assert "unnest" in conn.statements[0]
    assert conn.params[0] == ("s3", ["s3://a", "s3://b"], ["h1", "h2"])


def test_pgvector_reuses_one_connection_across_calls() -> None:
    """Two upserts must not open a second connection (see pgvector.py docstring)."""
    index = PgVectorIndex(dsn="postgresql://u@h/db")
    connect_calls = 0
    conn = _Conn(index)

    async def connect() -> _Conn:
        nonlocal connect_calls
        connect_calls += 1
        return conn

    index._connect = connect  # type: ignore[method-assign]

    async def run() -> None:
        chunk_a = Chunk(text="a", source_uri="s3://a")
        chunk_b = Chunk(text="b", source_uri="s3://b")
        await index.upsert(chunk_a, [0.1, 0.2], source_id="s3", artifact_hash="h1")
        await index.upsert(chunk_b, [0.1, 0.2], source_id="s3", artifact_hash="h2")

    import asyncio

    asyncio.run(run())

    assert connect_calls == 1


def test_pgvector_aclose_releases_connection() -> None:
    index = PgVectorIndex(dsn="postgresql://u@h/db")
    conn = _Conn(index)
    closed = False

    async def close() -> None:
        nonlocal closed
        closed = True

    conn.close = close  # type: ignore[method-assign]
    _swap(index, conn)

    async def run() -> None:
        await index.reconcile("s3", {})
        await index.aclose()

    import asyncio

    asyncio.run(run())

    assert closed
    assert index._connection is None


def test_pgvector_reconcile_empty_source_clears_all() -> None:
    index = PgVectorIndex(dsn="postgresql://u@h/db")
    conn = _Conn(index)
    _swap(index, conn)

    async def run() -> None:
        await index.reconcile("s3", {})

    import asyncio

    asyncio.run(run())

    assert "DELETE" in conn.statements[0]
    assert conn.params[0] == ("s3",)


def test_pgvector_upsert_batch_sends_one_multi_row_insert() -> None:
    index = PgVectorIndex(dsn="postgresql://u@h/db")
    conn = _Conn(index)
    _swap(index, conn)

    items = [
        (Chunk(text=f"chunk {i}", source_uri=f"u/{i}"), [0.1, 0.2], f"h{i}")
        for i in range(3)
    ]

    async def run() -> None:
        await index.upsert_batch(items, source_id="s")

    import asyncio

    asyncio.run(run())

    insert_statements = [s for s in conn.statements if s.startswith("INSERT")]
    assert len(insert_statements) == 1
    assert insert_statements[0].count("ON CONFLICT") == 1
    assert conn.params[-1].count(str(point_id("u/0", "chunk 0"))) == 1
    assert len(conn.params[-1]) == 3 * 7


def test_pgvector_upsert_batch_chunks_large_batches() -> None:
    from alvis.index.pgvector import _UPSERT_BATCH_SIZE

    index = PgVectorIndex(dsn="postgresql://u@h/db")
    conn = _Conn(index)
    _swap(index, conn)

    total = _UPSERT_BATCH_SIZE + 1
    items = [
        (Chunk(text=f"chunk {i}", source_uri=f"u/{i}"), [0.1, 0.2], f"h{i}")
        for i in range(total)
    ]

    async def run() -> None:
        await index.upsert_batch(items, source_id="s")

    import asyncio

    asyncio.run(run())

    insert_statements = [s for s in conn.statements if s.startswith("INSERT")]
    assert len(insert_statements) == 2
    assert conn.params[-2].count("::vector") == 0  # params are values, not SQL text


def test_pgvector_upsert_batch_empty_is_noop() -> None:
    index = PgVectorIndex(dsn="postgresql://u@h/db")
    conn = _Conn(index)
    _swap(index, conn)

    async def run() -> None:
        await index.upsert_batch([], source_id="s")

    import asyncio

    asyncio.run(run())

    assert conn.statements == []