"""Hybrid search: BM25 keyword scoring + Reciprocal Rank Fusion."""

from __future__ import annotations

import logging

import pytest

from alvis.core.models import Chunk, SearchHit
from alvis.index import MemoryIndex, QdrantIndex, reciprocal_rank_fusion
from alvis.index.keyword import bm25_scores, tokenize
from alvis.index.sqlite import SqliteIndex
from alvis.pipeline.runner import query_async


def test_tokenize_lowercases_and_strips_punctuation() -> None:
    assert tokenize("Error-4471, retry!") == ["error", "4471", "retry"]


def test_bm25_scores_favours_documents_with_more_query_term_hits() -> None:
    docs = [
        tokenize("the quick brown fox"),
        tokenize("fox fox fox everywhere you look, a fox"),
        tokenize("no relevant terms here at all"),
    ]
    scores = bm25_scores(tokenize("fox"), docs)
    assert scores[1] > scores[0] > scores[2]
    assert scores[2] == 0.0


def test_bm25_scores_empty_query_or_corpus() -> None:
    assert bm25_scores([], [["a"], ["b"]]) == [0.0, 0.0]
    assert bm25_scores(["a"], []) == []


def test_reciprocal_rank_fusion_rewards_consensus() -> None:
    x = SearchHit(text="x", source_uri="u/x", score=0.9)
    m = SearchHit(text="m", source_uri="u/m", score=0.8)
    y = SearchHit(text="y", source_uri="u/y", score=0.7)
    z = SearchHit(text="z", source_uri="u/z", score=0.9)
    w = SearchHit(text="w", source_uri="u/w", score=0.7)

    dense = [x, m, y]  # m ranks 2nd by vector similarity
    keyword = [z, m, w]  # m also ranks 2nd by keyword match

    fused = reciprocal_rank_fusion(dense, keyword)

    # m is the only hit ranked in both lists -> its fused score beats any
    # hit that only ranked 1st in a single list.
    assert fused[0].text == "m"
    assert {hit.text for hit in fused} == {"x", "m", "y", "z", "w"}


def test_reciprocal_rank_fusion_single_ranking_preserves_order() -> None:
    hits = [SearchHit(text=str(i), source_uri=f"u/{i}", score=1.0 - i * 0.1) for i in range(3)]
    fused = reciprocal_rank_fusion(hits)
    assert [hit.text for hit in fused] == ["0", "1", "2"]


async def test_memory_index_keyword_search_ranks_by_bm25() -> None:
    index = MemoryIndex()
    await index.upsert(
        Chunk(text="install alvis with pip install alvis", source_uri="u/install"),
        [0.0],
        source_id="s",
        artifact_hash="h",
    )
    await index.upsert(
        Chunk(text="completely unrelated networking content", source_uri="u/other"),
        [0.0],
        source_id="s",
        artifact_hash="h",
    )

    hits = await index.keyword_search("install alvis", top_k=2)

    assert hits[0].source_uri == "u/install"
    assert hits[0].score > hits[1].score


async def test_memory_index_keyword_search_respects_filters() -> None:
    index = MemoryIndex()
    await index.upsert(
        Chunk(text="install alvis", source_uri="u/eng", metadata={"space": "ENG"}),
        [0.0],
        source_id="s",
        artifact_hash="h",
    )
    await index.upsert(
        Chunk(text="install alvis", source_uri="u/hr", metadata={"space": "HR"}),
        [0.0],
        source_id="s",
        artifact_hash="h",
    )

    hits = await index.keyword_search("install alvis", top_k=5, filters={"space": "HR"})

    assert [hit.source_uri for hit in hits] == ["u/hr"]


async def test_sqlite_index_keyword_search_ranks_by_bm25(tmp_path) -> None:
    index = SqliteIndex(str(tmp_path / "db.sqlite"))
    await index.upsert(
        Chunk(text="install alvis with pip install alvis", source_uri="u/install"),
        [0.0],
        source_id="s",
        artifact_hash="h",
    )
    await index.upsert(
        Chunk(text="completely unrelated networking content", source_uri="u/other"),
        [0.0],
        source_id="s",
        artifact_hash="h",
    )

    hits = await index.keyword_search("install alvis", top_k=2)

    assert hits[0].source_uri == "u/install"
    assert hits[0].score > hits[1].score


async def test_query_async_hybrid_fuses_dense_and_keyword(tmp_path) -> None:
    from alvis import dsl

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\ninstall alvis with pip\n", encoding="utf-8")
    config = dsl.pipeline(
        dsl.fs(str(docs)),
        chunk=dsl.chunk(strategy="sections"),
        index=dsl.memory(),
    )
    indexer = MemoryIndex()
    from alvis import run_async

    await run_async(config, indexer=indexer)

    hits = await query_async(config, "install alvis", indexer=indexer, hybrid=True)

    assert hits
    assert hits[0].source_uri.endswith("a.md")


async def test_query_async_hybrid_falls_back_without_keyword_search(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(request):  # noqa: ANN001
        import httpx

        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "id": "1",
                        "score": 0.5,
                        "payload": {"__text": "t", "__uri": "u/1"},
                    }
                ]
            },
        )

    import httpx

    from alvis import dsl

    index = QdrantIndex(url="http://localhost:6333", collection="t")
    index.client.transport = httpx.MockTransport(handler)
    config = dsl.pipeline(dsl.fs("."), index=dsl.qdrant(url="http://x", collection="t"))

    with caplog.at_level(logging.WARNING):
        hits = await query_async(config, "anything", indexer=index, hybrid=True)

    assert hits and hits[0].source_uri == "u/1"
    assert any("falling back to dense-only" in message for message in caplog.messages)
