"""LLM-judge answer-quality scoring (:mod:`alvis.judge`)."""

from __future__ import annotations

import logging

import httpx
import pytest

from alvis.core.models import SearchHit
from alvis.judge import AnswerJudge, JudgeScore, _parse_score


def _hits() -> list[SearchHit]:
    return [
        SearchHit(text="Alvis re-runs are idempotent.", source_uri="docs/a.md", score=0.9),
        SearchHit(text="Reconcile prunes stale points.", source_uri="docs/b.md", score=0.8),
    ]


def test_parse_score_extracts_json_object() -> None:
    score = _parse_score(
        '{"faithfulness": 0.9, "relevancy": 0.8, "reason": "grounded in excerpt 1"}'
    )
    assert score.faithfulness == 0.9
    assert score.relevancy == 0.8
    assert score.reason == "grounded in excerpt 1"


def test_parse_score_extracts_from_surrounding_prose() -> None:
    score = _parse_score('Here you go: {"faithfulness": 1.0, "relevancy": 1.0} thanks')
    assert score.faithfulness == 1.0
    assert score.relevancy == 1.0


def test_parse_score_raises_on_missing_json() -> None:
    with pytest.raises(ValueError, match="no JSON object"):
        _parse_score("not a json object")


def test_judge_score_clamps_out_of_range_values() -> None:
    score = JudgeScore(faithfulness=1.5, relevancy=-0.3)
    assert score.faithfulness == 1.0
    assert score.relevancy == 0.0


async def test_answer_judge_scores_via_chat_completions() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"faithfulness": 0.85, "relevancy": 0.9, '
                                '"reason": "matches excerpt 1"}'
                            )
                        }
                    }
                ]
            },
        )

    judge = AnswerJudge(
        base_url="http://judge.local", model="m", transport=httpx.MockTransport(handler)
    )
    score = await judge.score("is it safe?", "Yes, re-runs are idempotent [1].", _hits())

    assert score.faithfulness == 0.85
    assert score.relevancy == 0.9
    assert score.reason == "matches excerpt 1"


async def test_answer_judge_fails_soft_on_http_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    judge = AnswerJudge(
        base_url="http://judge.local",
        model="m",
        retries=0,
        transport=httpx.MockTransport(handler),
    )

    with caplog.at_level(logging.WARNING):
        score = await judge.score("q", "a", _hits())

    assert score.faithfulness == 0.0
    assert score.relevancy == 0.0
    assert any("answer judge failed" in message for message in caplog.messages)


async def test_answer_judge_fails_soft_on_malformed_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "nonsense"}}]})

    judge = AnswerJudge(
        base_url="http://judge.local", model="m", transport=httpx.MockTransport(handler)
    )

    with caplog.at_level(logging.WARNING):
        score = await judge.score("q", "a", _hits())

    assert score.faithfulness == 0.0
    assert score.reason == "judge call failed"
