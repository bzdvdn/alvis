"""PostgreSQL/pgvector index — idempotent vector storage via psycopg 3.

Optional dependency: ``pip install winnow[pgindex]`` (psycopg[binary]).
Uses the ``vector`` column type from the pgvector extension, so the target
database must have it installed (``CREATE EXTENSION vector``).

Point IDs are deterministic uuids (see ``point_id``), so ``UPSERT``
overwrites identical content instead of accumulating; ``reconcile`` prunes
this source's rows whose ``artifact_hash`` no longer matches the latest run.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Mapping
from types import ModuleType
from typing import Any

from winnow.core.ids import point_id
from winnow.core.models import Chunk


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


_PSYCOPG = importlib.util.find_spec("psycopg") is not None
_PGINDEX_EXTRA = "install the optional extra: pip install winnow[pgindex]"


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
        table: str = "winnow_chunks",
    ) -> None:
        if dsn and dsn_env:
            raise ValueError("provide either dsn or dsn_env, not both")
        if dsn_env:
            import os

            dsn = os.environ.get(dsn_env)
            if not dsn:
                raise ValueError(f"environment variable {dsn_env!r} is not set")
        if not dsn:
            raise ValueError("pgvector index requires a 'dsn' (or 'dsn_env')")
        self.dsn = dsn
        self.table = table
        self._dimensions: int | None = None

    async def _connect(self) -> Any:
        if not _PSYCOPG:
            raise ValueError(f"pgvector requires psycopg ({_PGINDEX_EXTRA})")
        import psycopg

        return await psycopg.AsyncConnection.connect(self.dsn)

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
        async with await self._connect() as connection:
            await connection.execute(create)

    async def upsert(
        self,
        chunk: Chunk,
        vector: list[float],
        *,
        source_id: str,
        artifact_hash: str,
    ) -> None:
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
        async with await self._connect() as connection:
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

    async def reconcile(
        self,
        source_id: str,
        current: Mapping[str, str],
    ) -> None:
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
        async with await self._connect() as connection:
            await connection.execute(delete, params)


__all__ = ["PgVectorIndex"]