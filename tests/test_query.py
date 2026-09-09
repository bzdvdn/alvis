"""Search / retrieval behaviour across index backends and the query API."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import httpx
import pytest

from alvis import dsl, query, query_async, run, run_async
from alvis.config import ConfigError
from alvis.core.models import Chunk, SearchHit
from alvis.index import MemoryIndex, QdrantIndex


async def _seed_memory(points: list[tuple[str, list[float], dict[str, Any]]]) -> MemoryIndex:
    index = MemoryIndex()
    for text, vector, metadata in points:
        await index.upsert(
            Chunk(text=text, source_uri="u", metadata=metadata),
            vector,
            source_id="test",
            artifact_hash="h",
        )
    return index


async def test_memory_search_returns_best_first() -> None:
    index = await _seed_memory(
        [
            ("zero", [1.0, 0.0], {"heading": "a"}),
            ("half", [0.7071, 0.7071], {"heading": "b"}),
            ("negative", [-1.0, 0.0], {"heading": "c"}),
        ]
    )
    hits: list[SearchHit] = await index.search([1.0, 0.0], top_k=3)
    expected = 1.0 / math.sqrt(2.0)
    assert [hit.score for hit in hits] == [
        pytest.approx(1.0),
        pytest.approx(expected),
        pytest.approx(-1.0),
    ]
    assert hits[0].text == "zero"
    assert hits[2].metadata == {"heading": "c"}
    assert isinstance(hits[0], SearchHit)


async def test_memory_search_respects_top_k() -> None:
    index = await _seed_memory([("a", [1.0, 0.0], {}), ("b", [0.99, 0.0], {})])
    hits = await index.search([1.0, 0.0], top_k=1)
    assert len(hits) == 1
    assert hits[0].text == "a"


async def test_query_async_passes_filters_through_to_index() -> None:
    captured: dict[str, Any] = {}

    class _CapturingIndex:
        async def upsert(self, *args: object, **kwargs: object) -> None:
            raise NotImplementedError

        async def reconcile(self, *args: object, **kwargs: object) -> None:
            raise NotImplementedError

        async def search(
            self,
            vector: list[float],
            *,
            top_k: int = 5,
            filters: Any = None,
            principals: Any = None,
        ) -> list[SearchHit]:
            captured["filters"] = filters
            return []

    config = dsl.pipeline(dsl.fs("."), index=dsl.memory())
    await query_async(
        config, "doc", top_k=5, indexer=_CapturingIndex(), filters={"space": "HR"}
    )
    assert captured["filters"] == {"space": "HR"}


async def test_query_roundtrip_with_shared_memory_index(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "install.md").write_text(
        "# Installing\n\npip install alvis\n\n# Run\n\nalvis run pipeline.yaml\n",
        encoding="utf-8",
    )
    config = dsl.pipeline(
        dsl.fs(str(docs)),
        chunk=dsl.chunk(strategy="sections", max_tokens=500),
        index=dsl.memory(),
    )
    indexer = MemoryIndex()
    await run_async(config, indexer=indexer)
    hits = await query_async(config, "how do I install this?", top_k=2, indexer=indexer)
    assert hits
    assert hits[0].metadata["heading"] == "Installing"


def test_query_sync_roundtrip(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nmake cheese\n", encoding="utf-8")
    config = dsl.pipeline(
        dsl.fs(str(docs)),
        chunk=dsl.chunk(strategy="sections"),
        index=dsl.memory(),
    )
    indexer = MemoryIndex()
    run(config, indexer=indexer)
    assert query(config, "make cheese", indexer=indexer)


async def test_query_rejects_empty_text(tmp_path: Path) -> None:
    config = dsl.pipeline(dsl.fs(str(tmp_path)), index=dsl.memory())
    with pytest.raises(ConfigError, match="must not be empty"):
        await query_async(config, "   ", indexer=MemoryIndex())


async def test_query_requires_index() -> None:
    config = dsl.pipeline(dsl.fs("."))
    with pytest.raises(ConfigError, match="index"):
        await query_async(config, "something")


async def test_qdrant_search_parses_results_and_scores() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/points/search")
        assert b'"limit": 1' in request.content or b'"limit":1' in request.content
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "id": "111",
                        "score": 0.9123,
                        "payload": {
                            "__text": "hello world",
                            "__uri": "s3://a",
                            "__document_id": "s3://a",
                            "heading": "H",
                            "__source": "s3",
                            "__hash": "h",
                            "__schema": 1,
                        },
                    }
                ]
            },
        )

    index = QdrantIndex(url="http://localhost:6333", collection="t")
    index.client.transport = httpx.MockTransport(handler)

    hits = await index.search([0.1, 0.2], top_k=1)
    assert len(hits) == 1
    assert hits[0].text == "hello world"
    assert hits[0].source_uri == "s3://a"
    assert hits[0].metadata == {"heading": "H"}
    assert hits[0].score == pytest.approx(0.9123)


async def test_qdrant_search_sends_filter_payload() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"result": []})

    index = QdrantIndex(url="http://localhost:6333", collection="t")
    index.client.transport = httpx.MockTransport(handler)

    await index.search([0.1, 0.2], top_k=1, filters={"space": "HR"})

    assert captured["body"]["filter"] == {
        "must": [{"key": "space", "match": {"value": "HR"}}]
    }


def test_pgvector_search_builds_sql() -> None:
    import asyncio

    from alvis.index import PgVectorIndex

    class _Cursor:
        async def fetchall(self) -> list[tuple[object, ...]]:
            return [("doc text", "u/1", {"heading": "H"}, 0.8), ("other", "u/2", {}, 0.1)]

    class _Conn:
        closed = False

        def __init__(self) -> None:
            self.statements: list[str] = []
            self.params: list[tuple[Any, ...]] = []

        async def execute(
            self, sql: Any, params: tuple[Any, ...] | None = None
        ) -> _Cursor:
            self.statements.append(sql.as_string(None))
            self.params.append(params or ())
            return _Cursor()

    index = PgVectorIndex(dsn="postgresql://u@h/db")
    conn = _Conn()

    async def fake_connect() -> Any:
        return conn

    index._connect = fake_connect  # type: ignore[method-assign]

    async def search() -> list[SearchHit]:
        return await index.search([0.5, 0.5], top_k=2)

    hits = asyncio.run(search())
    assert hits[0].source_uri == "u/1"
    assert hits[0].score == pytest.approx(0.8)
    assert "<=>" in conn.statements[-1]
    assert conn.params[-1] == ("[0.5,0.5]", "[0.5,0.5]", 2)

    async def search_filtered() -> list[SearchHit]:
        return await index.search([0.5, 0.5], top_k=2, filters={"space": "HR"})

    asyncio.run(search_filtered())
    assert "metadata @> %s::jsonb" in conn.statements[-1]
    assert conn.params[-1] == ("[0.5,0.5]", '{"space": "HR"}', "[0.5,0.5]", 2)


def test_cli_query_reports_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from typer.testing import CliRunner

    from alvis.cli import app
    from alvis.index import MemoryIndex

    config = tmp_path / "p.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {tmp_path}\n"
        "  index:\n"
        "    type: memory\n",
        encoding="utf-8",
    )
    indexer = MemoryIndex()
    from alvis.embed.hash import HashEmbedder

    embedder = HashEmbedder()
    query_vector = asyncio.run(
        embedder.embed(Chunk(text="install alvis", source_uri="q"))
    )
    asyncio.run(
        indexer.upsert(
            Chunk(text="install alvis", source_uri="u/install", metadata={"heading": "H"}),
            query_vector,
            source_id="s",
            artifact_hash="h",
        )
    )

    def seeded(*args: object, **kwargs: object) -> object:
        return indexer

    monkeypatch.setattr("alvis.pipeline.runner.build_indexer", seeded)

    runner = CliRunner()
    result = runner.invoke(app, ["query", str(config), "--text", "install alvis"])
    assert result.exit_code == 0
    assert "u/install" in result.output
    assert "install alvis" in result.output

    result = runner.invoke(
        app,
        ["query", str(config), "--text", "install alvis", "--filter", "heading=OTHER"],
    )
    assert result.exit_code == 0
    assert "No matches found." in result.output


def test_cli_query_rejects_malformed_filter(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from alvis.cli import app

    config = tmp_path / "p.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {tmp_path}\n"
        "  index:\n"
        "    type: memory\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        app, ["query", str(config), "--text", "x", "--filter", "no-equals-sign"]
    )
    assert result.exit_code == 1
    assert "Error" in result.output


def test_cli_query_no_matches(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from alvis.cli import app

    config = tmp_path / "p.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {tmp_path}\n"
        "  index:\n"
        "    type: memory\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(app, ["query", str(config), "--text", "anything"])
    assert result.exit_code == 0
    assert "No matches found." in result.output


def test_cli_query_missing_config(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from alvis.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["query", str(tmp_path / "missing.yaml"), "--text", "x"]
    )
    assert result.exit_code == 1
    assert "Error" in result.output