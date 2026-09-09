"""SQLite index — a persistent, zero-infrastructure vector store.

For dev and small prototypes: unlike the in-memory index, points survive
process restarts (one file). No extra dependencies — the standard library's
``sqlite3`` is used with the same ``Indexer`` protocol as Qdrant/pgvector, so
``upsert``/``reconcile``/``search`` and idempotency behave identically.

Point IDs are deterministic (``point_id``), so re-upserting identical content
overwrites instead of duplicating. Searches are brute-force cosine similarity
over stored vectors, which is fine for dev-scale corpora.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path

from alvis.core.ids import point_id
from alvis.core.models import Chunk, SearchHit
from alvis.index._acl import acl_visible
from alvis.index._math import clamp, cosine_similarity
from alvis.index.keyword import bm25_scores, tokenize

_SCHEMA = """
CREATE TABLE IF NOT EXISTS points (
    id            TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    uri           TEXT NOT NULL,
    text          TEXT NOT NULL,
    metadata      TEXT NOT NULL,
    vector        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_points_source ON points(source_id);
"""


class SqliteIndex:
    """Persistent chroma-like index backed by a single SQLite file.

    Config keys: ``path`` (a ``.db`` file, or ``:memory:`` for ephemeral use).
    The table is created on demand; vectors are stored as JSON.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.executescript(_SCHEMA)
        return connection

    def _get_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = self._connect()
        return self._connection

    async def upsert(
        self,
        chunk: Chunk,
        vector: list[float],
        *,
        source_id: str,
        artifact_hash: str,
    ) -> None:
        """Insert or replace a chunk point (deterministic point ID)."""
        pid = str(point_id(chunk.source_uri, chunk.text))

        def _write() -> None:
            connection = self._get_connection()
            with self._lock:
                connection.execute(
                    """
                    INSERT INTO points (id, source_id, artifact_hash, uri, text, metadata, vector)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_id = excluded.source_id,
                        artifact_hash = excluded.artifact_hash,
                        uri = excluded.uri,
                        text = excluded.text,
                        metadata = excluded.metadata,
                        vector = excluded.vector
                    """,
                    (
                        pid,
                        source_id,
                        artifact_hash,
                        chunk.source_uri,
                        chunk.text,
                        json.dumps(chunk.metadata, ensure_ascii=False),
                        json.dumps(vector),
                    ),
                )
                connection.commit()

        await asyncio.to_thread(_write)

    async def reconcile(
        self,
        source_id: str,
        current: Mapping[str, str],
    ) -> None:
        """Prune this source's points whose uri/artifact_hash are no longer current."""
        def _run() -> None:
            connection = self._get_connection()
            with self._lock:
                rows = connection.execute(
                    "SELECT id, uri, artifact_hash FROM points WHERE source_id = ?",
                    (source_id,),
                ).fetchall()
                stale = [
                    row[0]
                    for row in rows
                    if row[1] not in current or current[row[1]] != row[2]
                ]
                if stale:
                    connection.executemany(
                        "DELETE FROM points WHERE id = ?",
                        [(pid,) for pid in stale],
                    )
                    connection.commit()

        await asyncio.to_thread(_run)

    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """Brute-force cosine similarity over stored points, best first.

        ``filters`` keeps only rows whose metadata matches every pair;
        applied before ranking (metadata is JSON, so filtered in Python).
        ``principals`` additionally drops rows whose ``acl`` metadata
        doesn't include any of them.
        """

        def _read() -> list[tuple[float, str, str, dict[str, object]]]:
            connection = self._get_connection()
            with self._lock:
                rows = connection.execute(
                    "SELECT text, uri, metadata, vector FROM points"
                ).fetchall()
            scored: list[tuple[float, str, str, dict[str, object]]] = []
            for text, uri, metadata_raw, vector_raw in rows:
                metadata = json.loads(metadata_raw)
                if filters and not all(
                    str(metadata.get(key)) == value for key, value in filters.items()
                ):
                    continue
                if not acl_visible(metadata, principals):
                    continue
                stored = json.loads(vector_raw)
                similarity = clamp(cosine_similarity(vector, stored))
                scored.append((similarity, text, uri, metadata))
            scored.sort(key=lambda item: item[0], reverse=True)
            return scored

        scored = await asyncio.to_thread(_read)
        return [
            SearchHit(
                text=text,
                source_uri=uri,
                metadata=metadata,
                score=score,
            )
            for score, text, uri, metadata in scored[:top_k]
        ]

    async def keyword_search(
        self,
        text: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """BM25 keyword search over stored points, best first."""

        def _read() -> list[tuple[str, str, dict[str, object]]]:
            connection = self._get_connection()
            with self._lock:
                rows = connection.execute(
                    "SELECT text, uri, metadata FROM points"
                ).fetchall()
            kept: list[tuple[str, str, dict[str, object]]] = []
            for row_text, uri, metadata_raw in rows:
                metadata = json.loads(metadata_raw)
                if filters and not all(
                    str(metadata.get(key)) == value for key, value in filters.items()
                ):
                    continue
                if not acl_visible(metadata, principals):
                    continue
                kept.append((row_text, uri, metadata))
            return kept

        rows = await asyncio.to_thread(_read)
        query_tokens = tokenize(text)
        scores = bm25_scores(query_tokens, [tokenize(row[0]) for row in rows])
        ranked = sorted(zip(rows, scores, strict=True), key=lambda item: item[1], reverse=True)
        return [
            SearchHit(text=row_text, source_uri=uri, metadata=metadata, score=score)
            for (row_text, uri, metadata), score in ranked[:top_k]
        ]

    async def count(self) -> int:
        """Total number of stored points."""

        def _count() -> int:
            connection = self._get_connection()
            with self._lock:
                row = connection.execute("SELECT COUNT(*) FROM points").fetchone()
            return int(row[0]) if row else 0

        return await asyncio.to_thread(_count)
