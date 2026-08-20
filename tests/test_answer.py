"""Response-synthesis tests — citing answers over retrieval hits.

Uses the same ``MockServer`` fixture connectors use, which doubles as proof
the fixture generalizes beyond sources.
"""

from __future__ import annotations

import json
from typing import Any

from alvis.answer import Synthesizer, build_answer, citation_answer
from alvis.core.models import SearchHit
from alvis.testing import MockServer


def _hits() -> list[SearchHit]:
    return [
        SearchHit(
            text="Alvis re-runs are idempotent: identical content is overwritten.",
            source_uri="docs/idempotency.md",
            metadata={"title": "idempotency"},
            score=0.91,
        ),
        SearchHit(
            text="A reconcile pass prunes points of changed or deleted documents.",
            source_uri="docs/reconcile.md",
            metadata={"title": "reconcile"},
            score=0.88,
        ),
        SearchHit(
            text="The YAML contract is versioned as schema v1.",
            source_uri="docs/schema.md",
            metadata={"title": "schema"},
            score=0.7,
        ),
    ]


async def test_synthesizer_answers_and_builds_citations() -> None:
    server = MockServer()
    server.on(
        "POST",
        "/chat/completions",
        json_payload={
            "choices": [
                {
                    "message": {
                        "content": (
                            "Re-runs are idempotent [1], and stale points are "
                            "pruned by reconcile [2]."
                        )
                    }
                }
            ]
        },
    )

    llm = Synthesizer(
        base_url="http://llm.local",
        model="gpt-test",
        transport=server.transport,
    )
    answer = await llm.answer("is re-running safe?", _hits())

    assert answer.text.startswith("Re-runs are idempotent")
    assert [c.index for c in answer.citations] == [1, 2]
    assert [c.source_uri for c in answer.citations] == [
        "docs/idempotency.md",
        "docs/reconcile.md",
    ]

    payload: dict[str, Any] = json.loads(server.requests[0].content)
    assert payload["model"] == "gpt-test"
    messages = payload["messages"]
    assert messages[0]["role"] == "system"
    assert "[1]" in messages[1]["content"]
    assert "docs/idempotency.md" in messages[1]["content"]


async def test_synthesizer_cites_only_referenced_excerpts() -> None:
    server = MockServer()
    server.on(
        "POST",
        "/chat/completions",
        json_payload={
            "choices": [{"message": {"content": "The contract lives in docs/schema.md [3]."}}]
        },
    )
    llm = Synthesizer(
        base_url="http://llm.local",
        model="gpt-test",
        transport=server.transport,
    )
    answer = await llm.answer("where is the contract?", _hits())

    assert [c.index for c in answer.citations] == [3]
    assert [c.source_uri for c in answer.citations] == ["docs/schema.md"]


def test_build_answer_ignores_out_of_range_markers() -> None:
    answer = build_answer("Nothing relevant [1] [9] [0]", _hits())
    assert [c.index for c in answer.citations] == [1]


def test_citation_answer_is_offline_fallback() -> None:
    answer = citation_answer("where is the contract?", _hits())
    assert len(answer.citations) == 3
    assert "[1]" in answer.text
    assert "docs/idempotency.md" in answer.text
    assert answer.citations[0].snippet


def test_synthesizer_signature() -> None:
    llm = Synthesizer(base_url="http://llm.local", model="gpt-42")
    assert llm.signature == "openai-chat:gpt-42"