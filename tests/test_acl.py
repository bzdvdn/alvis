"""ACL-aware retrieval: source config -> chunk metadata -> index filtering."""

from __future__ import annotations

import httpx

from alvis import dsl, run_async
from alvis.core.models import Chunk
from alvis.index import MemoryIndex, QdrantIndex
from alvis.index._acl import acl_visible
from alvis.index.sqlite import SqliteIndex
from alvis.pipeline.engine import pipeline_signature
from alvis.pipeline.runner import query_async


def test_acl_visible_no_principals_means_unrestricted() -> None:
    assert acl_visible({"acl": ["eng"]}, None) is True


def test_acl_visible_no_acl_metadata_is_public() -> None:
    assert acl_visible({}, ["eng"]) is True


def test_acl_visible_intersecting_principal_matches() -> None:
    assert acl_visible({"acl": ["eng", "ops"]}, ["ops", "sales"]) is True


def test_acl_visible_disjoint_principals_excluded() -> None:
    assert acl_visible({"acl": ["eng"]}, ["sales"]) is False


def test_acl_visible_empty_acl_list_is_public() -> None:
    assert acl_visible({"acl": []}, ["sales"]) is True


async def _seeded_index(acl: list[str] | None) -> tuple[MemoryIndex, object]:
    indexer = MemoryIndex()
    metadata = {"acl": acl} if acl else {}
    await indexer.upsert(
        Chunk(text="restricted doc", source_uri="u/restricted", metadata=metadata),
        [1.0, 0.0],
        source_id="s",
        artifact_hash="h",
    )
    await indexer.upsert(
        Chunk(text="public doc", source_uri="u/public"),
        [1.0, 0.0],
        source_id="s",
        artifact_hash="h",
    )
    return indexer, None


async def test_memory_search_excludes_restricted_without_matching_principal() -> None:
    indexer, _ = await _seeded_index(["eng"])
    hits = await indexer.search([1.0, 0.0], top_k=5, principals=["sales"])
    assert {hit.source_uri for hit in hits} == {"u/public"}


async def test_memory_search_includes_restricted_with_matching_principal() -> None:
    indexer, _ = await _seeded_index(["eng"])
    hits = await indexer.search([1.0, 0.0], top_k=5, principals=["eng"])
    assert {hit.source_uri for hit in hits} == {"u/restricted", "u/public"}


async def test_memory_search_no_principals_sees_everything() -> None:
    indexer, _ = await _seeded_index(["eng"])
    hits = await indexer.search([1.0, 0.0], top_k=5)
    assert {hit.source_uri for hit in hits} == {"u/restricted", "u/public"}


async def test_memory_keyword_search_respects_principals() -> None:
    indexer, _ = await _seeded_index(["eng"])
    hits = await indexer.keyword_search("doc", top_k=5, principals=["sales"])
    assert {hit.source_uri for hit in hits} == {"u/public"}


async def test_sqlite_search_respects_principals(tmp_path) -> None:
    index = SqliteIndex(str(tmp_path / "db.sqlite"))
    await index.upsert(
        Chunk(text="restricted", source_uri="u/r", metadata={"acl": ["eng"]}),
        [1.0, 0.0],
        source_id="s",
        artifact_hash="h",
    )
    await index.upsert(
        Chunk(text="public", source_uri="u/p"),
        [1.0, 0.0],
        source_id="s",
        artifact_hash="h",
    )

    excluded = await index.search([1.0, 0.0], top_k=5, principals=["sales"])
    included = await index.search([1.0, 0.0], top_k=5, principals=["eng"])

    assert {hit.source_uri for hit in excluded} == {"u/p"}
    assert {hit.source_uri for hit in included} == {"u/r", "u/p"}


async def test_qdrant_search_sends_acl_should_clause() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"result": []})

    index = QdrantIndex(url="http://localhost:6333", collection="t")
    index.client.transport = httpx.MockTransport(handler)

    await index.search([0.1, 0.2], top_k=1, principals=["eng"])

    body = captured["body"]
    assert isinstance(body, dict)
    must = body["filter"]["must"]
    assert {"is_empty": {"key": "__acl"}} in must[0]["should"]
    assert {"key": "__acl", "match": {"any": ["eng"]}} in must[0]["should"]


def test_pgvector_search_builds_acl_where_clause() -> None:
    import asyncio

    from alvis.index import PgVectorIndex

    class _Cursor:
        async def fetchall(self):  # noqa: ANN201
            return []

    class _Conn:
        closed = False

        def __init__(self) -> None:
            self.statements: list[str] = []
            self.params: list[tuple] = []

        async def execute(self, sql, params=None):  # noqa: ANN001
            self.statements.append(sql.as_string(None))
            self.params.append(params or ())
            return _Cursor()

    index = PgVectorIndex(dsn="postgresql://u@h/db")
    conn = _Conn()

    async def fake_connect():  # noqa: ANN202
        return conn

    index._connect = fake_connect  # type: ignore[method-assign]

    async def search():  # noqa: ANN202
        return await index.search([0.5, 0.5], top_k=2, principals=["eng"])

    asyncio.run(search())

    assert "?|" in conn.statements[-1]
    assert conn.params[-1] == ("[0.5,0.5]", ["eng"], "[0.5,0.5]", 2)


def test_pipeline_signature_changes_with_source_acl() -> None:
    base = dsl.pipeline(dsl.fs("."), index=dsl.memory())
    with_acl = dsl.pipeline(dsl.fs(".", acl=["eng"]), index=dsl.memory())
    assert pipeline_signature(base) != pipeline_signature(with_acl)


async def test_fetch_stage_stamps_acl_onto_chunks(tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nsecret content\n", encoding="utf-8")

    config = dsl.pipeline(
        dsl.fs(str(docs), acl=["eng"]),
        chunk=dsl.chunk(strategy="sections"),
        index=dsl.memory(),
    )
    indexer = MemoryIndex()
    await run_async(config, indexer=indexer)

    chunk, _ = indexer.entries()[0]
    assert chunk.metadata["acl"] == ["eng"]


async def test_query_async_enforces_acl_end_to_end(tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nsecret content\n", encoding="utf-8")

    config = dsl.pipeline(
        dsl.fs(str(docs), acl=["eng"]),
        chunk=dsl.chunk(strategy="sections"),
        index=dsl.memory(),
    )
    indexer = MemoryIndex()
    await run_async(config, indexer=indexer)

    outsider_hits = await query_async(
        config, "secret content", indexer=indexer, principals=["sales"]
    )
    eng_hits = await query_async(
        config, "secret content", indexer=indexer, principals=["eng"]
    )
    unrestricted_hits = await query_async(config, "secret content", indexer=indexer)

    assert outsider_hits == []
    assert len(eng_hits) == 1
    assert len(unrestricted_hits) == 1


def test_acl_config_not_forwarded_to_source_constructor(tmp_path) -> None:
    """acl must never reach FilesystemSource.__init__ as an unexpected kwarg."""
    from alvis.config.models import SourceConfig
    from alvis.factories import build_source

    config = SourceConfig(type="fs", config={"path": str(tmp_path), "acl": ["eng"]})
    source = build_source(config)
    assert source is not None
