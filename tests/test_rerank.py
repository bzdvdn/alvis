"""LLM-based reranking (:mod:`alvis.rerank`)."""

from __future__ import annotations

import logging

import httpx
import pytest

from alvis import dsl, run_async
from alvis.core.models import SearchHit
from alvis.index import MemoryIndex
from alvis.pipeline.runner import query_async
from alvis.rerank import LLMReranker, _parse_order


def _hits(*texts: str) -> list[SearchHit]:
    return [
        SearchHit(text=text, source_uri=f"u/{i}", score=1.0 - i * 0.1)
        for i, text in enumerate(texts)
    ]


def test_parse_order_accepts_permutation() -> None:
    assert _parse_order("[3, 1, 2]", 3) == [2, 0, 1]


def test_parse_order_rejects_non_permutation() -> None:
    with pytest.raises(ValueError, match="not a permutation"):
        _parse_order("[1, 1, 2]", 3)


def test_parse_order_rejects_missing_array() -> None:
    with pytest.raises(ValueError, match="no JSON array"):
        _parse_order("sure, here you go", 3)


def test_parse_order_extracts_array_from_prose() -> None:
    assert _parse_order("Sure! Here it is: [2, 1] — done.", 2) == [1, 0]


async def test_llm_reranker_reorders_hits_by_model_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "[2, 1, 3]"}}]},
        )

    reranker = LLMReranker(
        base_url="http://x", model="m", transport=httpx.MockTransport(handler)
    )
    hits = _hits("a", "b", "c")

    ranked = await reranker.rerank("q", hits, top_k=3)

    assert [hit.text for hit in ranked] == ["b", "a", "c"]


async def test_llm_reranker_caps_excerpt_length_in_prompt() -> None:
    from alvis.rerank import _MAX_EXCERPT_CHARS, _prompt

    long_hit = SearchHit(text="x" * (_MAX_EXCERPT_CHARS + 500), source_uri="u/long", score=0.5)
    prompt = _prompt("q", [long_hit])
    excerpt = prompt.split("Excerpts:\n[1]\n", 1)[1]

    assert len(excerpt) <= _MAX_EXCERPT_CHARS + 1  # + truncation marker
    assert excerpt.endswith("…")


async def test_llm_reranker_short_excerpt_is_not_truncated() -> None:
    from alvis.rerank import _prompt

    hit = SearchHit(text="short excerpt", source_uri="u/1", score=0.5)
    prompt = _prompt("q", [hit])
    assert "short excerpt" in prompt
    assert "…" not in prompt


async def test_llm_reranker_truncates_to_top_k() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "[1, 2, 3]"}}]},
        )

    reranker = LLMReranker(
        base_url="http://x", model="m", transport=httpx.MockTransport(handler)
    )
    hits = _hits("a", "b", "c")

    ranked = await reranker.rerank("q", hits, top_k=1)

    assert [hit.text for hit in ranked] == ["a"]


async def test_llm_reranker_empty_hits_short_circuits() -> None:
    reranker = LLMReranker(base_url="http://x", model="m")
    assert await reranker.rerank("q", [], top_k=5) == []


async def test_llm_reranker_falls_back_on_malformed_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not json at all"}}]},
        )

    reranker = LLMReranker(
        base_url="http://x", model="m", transport=httpx.MockTransport(handler)
    )
    hits = _hits("a", "b", "c")

    with caplog.at_level(logging.WARNING):
        ranked = await reranker.rerank("q", hits, top_k=2)

    assert [hit.text for hit in ranked] == ["a", "b"]
    assert any("rerank failed" in message for message in caplog.messages)


async def test_llm_reranker_falls_back_on_http_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    reranker = LLMReranker(
        base_url="http://x",
        model="m",
        retries=0,
        transport=httpx.MockTransport(handler),
    )
    hits = _hits("a", "b")

    ranked = await reranker.rerank("q", hits, top_k=5)

    assert [hit.text for hit in ranked] == ["a", "b"]


async def test_query_async_rerank_reorders_and_narrows(tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nalpha content\n", encoding="utf-8")
    (docs / "b.md").write_text("# B\n\nbeta content\n", encoding="utf-8")
    config = dsl.pipeline(
        dsl.fs(str(docs)),
        chunk=dsl.chunk(strategy="sections"),
        index=dsl.memory(),
    )
    indexer = MemoryIndex()
    await run_async(config, indexer=indexer)

    async def handler(request: httpx.Request) -> httpx.Response:
        # 2 chunks are indexed (one section per file); reverse whatever the
        # dense ranking sent over.
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "[2, 1]"}}]}
        )

    reranker = LLMReranker(
        base_url="http://x", model="m", transport=httpx.MockTransport(handler)
    )

    dense_hits = await query_async(config, "content", indexer=indexer, top_k=2)
    reranked = await query_async(
        config, "content", indexer=indexer, top_k=1, rerank=reranker
    )

    assert len(reranked) == 1
    assert reranked[0].text == dense_hits[1].text
