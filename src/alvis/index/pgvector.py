"""PostgreSQL/pgvector index — idempotent vector storage via psycopg 3.

Optional dependency: ``pip install alvis[pgindex]`` (psycopg[binary]).
Uses the ``vector`` column type from the pgvector extension, so the target
database must have it installed (``CREATE EXTENSION vector``).

Point IDs are deterministic uuids (see ``point_id``), so ``UPSERT``
overwrites identical content instead of accumulating; ``reconcile`` prunes
this source's rows whose ``artifact_hash`` no longer matches the latest run.

One connection is opened lazily and reused for the lifetime of the index
(autocommit, so no per-statement transaction bookkeeping) — the previous
"connect, run one statement, disconnect" pattern meant one full TCP+auth
round trip *per chunk* on a large ingestion run. An ``asyncio.Lock`` guards
it, since a single ``psycopg`` connection isn't safe for concurrent use;
call :meth:`PgVectorIndex.aclose` when done with the index (a fresh
``run()``/``query()`` call does this for you).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from collections.abc import Mapping, Sequence
from types import ModuleType
from typing import Any

from alvis.core.ids import point_id
from alvis.core.models import Chunk, SearchHit
from alvis.secrets import resolve_secret


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


_UPSERT_BATCH_SIZE = 500
"""Rows per multi-row ``INSERT`` in :meth:`PgVectorIndex.upsert_batch`."""


_PSYCOPG = importlib.util.find_spec("psycopg") is not None
_PGINDEX_EXTRA = "install the optional extra: pip install alvis[pgindex]"


def _psql() -> ModuleType:
    if not _PSYCOPG:
        raise ValueError(f"pgvector requires psycopg ({_PGINDEX_EXTRA})")
    import psycopg

    return psycopg.sql


class PgVectorIndex:
    """Stores chunk vectors in a ``pgvector`` column (type ``pgvector``)."""

    def __init__(
        self,
        dsn: str | None = None,
        dsn_env: str | None = None,
        table: str = "alvis_chunks",
    ) -> None:
        if dsn and dsn_env:
            raise ValueError("provide either dsn or dsn_env, not both")
        if dsn_env:
            dsn = resolve_secret(dsn_env)
            if not dsn:
                raise ValueError(f"secret {dsn_env!r} is not set")
        if not dsn:
            raise ValueError("pgvector index requires a 'dsn' (or 'dsn_env')")
        self.dsn = dsn
        self.table = table
        self._dimensions: int | None = None
        self._connection: Any | None = None
        self._connection_lock = asyncio.Lock()

    async def _connect(self) -> Any:
        if not _PSYCOPG:
            raise ValueError(f"pgvector requires psycopg ({_PGINDEX_EXTRA})")
        import psycopg

        return await psycopg.AsyncConnection.connect(self.dsn, autocommit=True)

    async def _get_connection(self) -> Any:
        """Return the shared connection, (re)connecting if needed."""
        async with self._connection_lock:
            if self._connection is None or self._connection.closed:
                self._connection = await self._connect()
            return self._connection

    async def aclose(self) -> None:
        """Close the shared connection, if one was ever opened."""
        async with self._connection_lock:
            if self._connection is not None and not self._connection.closed:
                await self._connection.close()
            self._connection = None

    def _table(self) -> Any:
        return _psql().Identifier(self.table)

    async def _ensure_table(self, dimensions: int) -> None:
        if self._dimensions is None:
            self._dimensions = dimensions
        elif self._dimensions != dimensions:
            raise ValueError(
                f"pgvector table {self.table!r} is {self._dimensions}-dimensional, "
                f"but a {dimensions}-dimensional vector arrived"
            )
        connection = await self._get_connection()
        create = _psql().SQL("CREATE EXTENSION IF NOT EXISTS vector")
        await connection.execute(create)
        create = _psql().SQL(
            "CREATE TABLE IF NOT EXISTS {} ("
            "id text PRIMARY KEY, "
            "source_uri text NOT NULL, "
            "text text NOT NULL, "
            "source text NOT NULL, "
            "artifact_hash text NOT NULL, "
            "embedding vector({}) NOT NULL, "
            "metadata jsonb NOT NULL DEFAULT '{{}}'"
            ")"
        ).format(self._table(), _psql().Literal(dimensions))
        await connection.execute(create)
        index = _psql().SQL(
            "CREATE INDEX IF NOT EXISTS {} ON {} USING GIN (to_tsvector('english', text))"
        ).format(
            _psql().Identifier(f"{self.table}_text_fts"),
            self._table(),
        )
        await connection.execute(index)

    async def upsert(
        self,
        chunk: Chunk,
        vector: list[float],
        *,
        source_id: str,
        artifact_hash: str,
    ) -> None:
        """Insert or replace a chunk row (deterministic id, ``ON CONFLICT``)."""
        await self._ensure_table(len(vector))
        statement = _psql().SQL(
            "INSERT INTO {} (id, source_uri, text, source, artifact_hash, embedding, metadata) "
            "VALUES (%s, %s, %s, %s, %s, %s::vector, %s::jsonb) "
            "ON CONFLICT (id) DO UPDATE SET "
            "source_uri = EXCLUDED.source_uri, "
            "text = EXCLUDED.text, "
            "source = EXCLUDED.source, "
            "artifact_hash = EXCLUDED.artifact_hash, "
            "embedding = EXCLUDED.embedding, "
            "metadata = EXCLUDED.metadata"
        ).format(self._table())
        vector_literal = _vector_literal(vector)
        metadata = json.dumps(chunk.metadata)
        connection = await self._get_connection()
        await connection.execute(
            statement,
            (
                str(point_id(chunk.source_uri, chunk.text)),
                chunk.source_uri,
                chunk.text,
                source_id,
                artifact_hash,
                vector_literal,
                metadata,
            ),
        )

    async def upsert_batch(
        self,
        items: Sequence[tuple[Chunk, list[float], str]],
        *,
        source_id: str,
    ) -> None:
        """Upsert many chunk rows via multi-row ``INSERT`` statements.

        The shared connection already removed the per-call connect/auth
        round trip (see module docstring); this removes the per-*row*
        statement round trip on top of that — one multi-row ``INSERT`` per
        :data:`_UPSERT_BATCH_SIZE` chunks instead of one per chunk.
        """
        if not items:
            return
        await self._ensure_table(len(items[0][1]))
        connection = await self._get_connection()
        for start in range(0, len(items), _UPSERT_BATCH_SIZE):
            batch = items[start : start + _UPSERT_BATCH_SIZE]
            placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s::vector, %s::jsonb)"] * len(batch))
            params: list[object] = []
            for chunk, vector, artifact_hash in batch:
                params.extend(
                    (
                        str(point_id(chunk.source_uri, chunk.text)),
                        chunk.source_uri,
                        chunk.text,
                        source_id,
                        artifact_hash,
                        _vector_literal(vector),
                        json.dumps(chunk.metadata),
                    )
                )
            statement = _psql().SQL(
                "INSERT INTO {} (id, source_uri, text, source, artifact_hash, embedding, metadata) "
                "VALUES " + placeholders + " "
                "ON CONFLICT (id) DO UPDATE SET "
                "source_uri = EXCLUDED.source_uri, "
                "text = EXCLUDED.text, "
                "source = EXCLUDED.source, "
                "artifact_hash = EXCLUDED.artifact_hash, "
                "embedding = EXCLUDED.embedding, "
                "metadata = EXCLUDED.metadata"
            ).format(self._table())
            await connection.execute(statement, params)

    async def reconcile(
        self,
        source_id: str,
        current: Mapping[str, str],
    ) -> None:
        """Delete this source's rows not present in ``current``."""
        if current:
            delete = _psql().SQL(
                "DELETE FROM {} WHERE source = %s AND "
                "(source_uri, artifact_hash) NOT IN "
                "(SELECT * FROM unnest(%s::text[], %s::text[]))"
            ).format(self._table())
            params: tuple[object, ...] = (
                source_id,
                list(current.keys()),
                [current[key] for key in current],
            )
        else:
            delete = _psql().SQL("DELETE FROM {} WHERE source = %s").format(
                self._table()
            )
            params = (source_id,)
        connection = await self._get_connection()
        await connection.execute(delete, params)

    async def count(self) -> int:
        """Total rows in the table (for ``alvis status``)."""
        statement = _psql().SQL("SELECT count(*) FROM {}").format(self._table())
        connection = await self._get_connection()
        cursor = await connection.execute(statement)
        row = await cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """Nearest-neighbour search via cosine distance ``<=>``, best first.

        Score is ``1 - cosine_distance`` (cosine similarity). psycopg does not
        serialise floats into the ``vector`` cast, so the literal is inlined.
        ``filters`` becomes a ``metadata @> %s::jsonb`` containment check.
        ``principals``, when given, additionally requires a row to have no
        ``acl`` metadata (public) or an ``acl`` array that overlaps
        ``principals`` (jsonb ``?|``) — both applied server-side before
        ranking.
        """
        literal = _vector_literal(vector)
        conditions: list[Any] = []
        params: list[object] = [literal]
        if filters:
            conditions.append(_psql().SQL("metadata @> %s::jsonb"))
            params.append(json.dumps(dict(filters)))
        if principals is not None:
            conditions.append(
                _psql().SQL(
                    "(NOT (metadata ? 'acl') OR jsonb_array_length(metadata->'acl') = 0 "
                    "OR metadata->'acl' ?| %s::text[])"
                )
            )
            params.append(list(principals))
        where = _psql().SQL("")
        if conditions:
            where = _psql().SQL("WHERE {}").format(_psql().SQL(" AND ").join(conditions))
        params.append(literal)
        statement = _psql().SQL(
            "SELECT text, source_uri, metadata, "
            "1 - (embedding <=> %s::vector) AS score "
            "FROM {} {} ORDER BY embedding <=> %s::vector LIMIT %s"
        ).format(self._table(), where)
        connection = await self._get_connection()
        cursor = await connection.execute(statement, (*params, top_k))
        rows = await cursor.fetchall()
        return [
            SearchHit(
                text=row[0],
                source_uri=row[1],
                metadata=dict(row[2]),
                score=float(row[3]),
            )
            for row in rows
        ]

    async def keyword_search(
        self,
        text: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """Full-text ranking via Postgres ``tsvector``/``ts_rank``, best first.

        ``to_tsvector('english', text) @@ plainto_tsquery(...)`` restricts to
        matching rows server-side (backed by the GIN index from
        :meth:`_ensure_table`); ``ts_rank`` orders them. Unlike ``memory``/
        ``sqlite`` (which BM25-score the whole corpus in Python), ranking
        happens in the database — no local scoring pass needed.
        ``filters``/``principals`` apply the same server-side clauses as
        :meth:`search`.
        """
        select_params: list[object] = [text]
        conditions: list[Any] = [
            _psql().SQL("to_tsvector('english', text) @@ plainto_tsquery('english', %s)")
        ]
        where_params: list[object] = [text]
        if filters:
            conditions.append(_psql().SQL("metadata @> %s::jsonb"))
            where_params.append(json.dumps(dict(filters)))
        if principals is not None:
            conditions.append(
                _psql().SQL(
                    "(NOT (metadata ? 'acl') OR jsonb_array_length(metadata->'acl') = 0 "
                    "OR metadata->'acl' ?| %s::text[])"
                )
            )
            where_params.append(list(principals))
        where = _psql().SQL(" AND ").join(conditions)
        statement = _psql().SQL(
            "SELECT text, source_uri, metadata, "
            "ts_rank(to_tsvector('english', text), plainto_tsquery('english', %s)) AS score "
            "FROM {} WHERE {} ORDER BY score DESC LIMIT %s"
        ).format(self._table(), where)
        connection = await self._get_connection()
        try:
            cursor = await connection.execute(
                statement, (*select_params, *where_params, top_k)
            )
        except Exception:
            return []
        rows = await cursor.fetchall()
        return [
            SearchHit(
                text=row[0],
                source_uri=row[1],
                metadata=dict(row[2]),
                score=float(row[3]),
            )
            for row in rows
        ]


__all__ = ["PgVectorIndex"]