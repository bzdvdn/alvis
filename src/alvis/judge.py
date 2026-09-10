"""LLM-judge based answer-quality evaluation — faithfulness & relevancy.

:mod:`alvis.evaluation` measures *retrieval* quality: did the right chunk
come back. This module measures the next step: given a synthesized
:class:`~alvis.answer.Answer`, did its claims actually follow from the
retrieved excerpts (faithfulness), and did it address the question asked
(relevancy)? Unlike the retrieval harness, this always needs an LLM — a
"did this text hallucinate" judgment isn't reducible to a formula the
project's zero-local-ML-dependency philosophy could compute from scratch
(see CONSTITUTION.md); it is a genuinely separate, opt-in axis of
evaluation, not a replacement for the retrieval-only harness.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from alvis.core.models import SearchHit
from alvis.observability import LOG
from alvis.sources.http import HttpClient

_SYSTEM_PROMPT = (
    "You are grading a RAG system's answer against the excerpts it was given. "
    "Score two things on a 0.0-1.0 scale:\n"
    "- faithfulness: does every claim in the answer follow from the excerpts, "
    "with no unsupported invention?\n"
    "- relevancy: does the answer actually address the question asked?\n"
    'Respond with ONLY a JSON object: {"faithfulness": <0-1>, "relevancy": <0-1>, '
    '"reason": "<one short sentence>"}. No prose, no markdown fences — the '
    "object alone."
)


class JudgeScore(BaseModel):
    """One LLM-judge verdict over a single (question, answer, excerpts) triple."""

    model_config = ConfigDict(frozen=True)

    faithfulness: float
    relevancy: float
    reason: str = ""

    @field_validator("faithfulness", "relevancy")
    @classmethod
    def _clamp_unit_interval(cls, value: float) -> float:
        return max(0.0, min(1.0, value))


class AnswerJudge:
    """Scores a synthesized answer via an OpenAI-compatible chat endpoint.

    Config mirrors :class:`alvis.answer.Synthesizer` and
    :class:`alvis.rerank.LLMReranker`: ``base_url`` (any OpenAI-compatible
    server), ``model``, optional ``api_token_env`` — deliberately a
    separate client/model from the one that *synthesized* the answer, so a
    stronger or independent model can grade a cheaper one's output.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_token_env: str | None = None,
        timeout: float = 60.0,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        transport: Any = None,
        temperature: float = 0.0,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.client = HttpClient(
            base_url=base_url,
            api_token_env=api_token_env,
            timeout=timeout,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
            transport=transport,
        )

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self.client.aclose()

    async def score(
        self,
        question: str,
        answer_text: str,
        hits: Sequence[SearchHit],
    ) -> JudgeScore:
        """Judge one answer against the excerpts it was synthesized from.

        Never raises: a failed call or an unparseable response is logged
        and scored ``faithfulness=0.0, relevancy=0.0`` rather than crashing
        an eval run — one bad judge call must not kill a whole batch.
        """
        try:
            payload: dict[str, Any] = {
                "model": self.model,
                "temperature": self.temperature,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": _prompt(question, answer_text, hits)},
                ],
            }
            response = await self.client.request(
                "POST",
                "/chat/completions",
                payload=payload,
                ok_status=(200,),
            )
            content = str(response["choices"][0]["message"]["content"])
            return _parse_score(content)
        except Exception:
            LOG.warning("answer judge failed; scoring 0", exc_info=True)
            return JudgeScore(faithfulness=0.0, relevancy=0.0, reason="judge call failed")


def _prompt(question: str, answer_text: str, hits: Sequence[SearchHit]) -> str:
    excerpts = "\n\n".join(
        f"[{index}]\n{hit.text.strip()}" for index, hit in enumerate(hits, start=1)
    )
    return (
        f"Question: {question}\n\n"
        f"Excerpts given to the system:\n{excerpts}\n\n"
        f"System's answer:\n{answer_text}"
    )


def _parse_score(content: str) -> JudgeScore:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object found in judge response: {content!r}")
    data = json.loads(match.group(0))
    return JudgeScore(
        faithfulness=float(data["faithfulness"]),
        relevancy=float(data["relevancy"]),
        reason=str(data.get("reason", "")),
    )


__all__ = ["AnswerJudge", "JudgeScore"]
