"""Retrieval-quality evaluation harness.

Ingestion has golden-snapshot tests (:mod:`alvis.testing`); retrieval had
nothing proving a pipeline's chunking/embedding choices actually retrieve the
right content. This module closes that gap with a small, LLM-free harness:

- :class:`EvalCase` — a query paired with the source document it should
  retrieve (``expected_source_uri``, matched as a substring of
  ``SearchHit.source_uri``).
- :func:`evaluate_async` / :func:`evaluate` — run every case against a
  pipeline's configured index and report hit rate + mean reciprocal rank
  (MRR).
- :func:`load_cases` — read cases from a YAML file (a plain list of
  ``{query, expected_source_uri}`` mappings).

By default this proves *retrieval* quality (did the right chunk come
back), not *answer* quality. Passing both ``llm`` and ``judge`` opts into
the second, separate axis: each case's hits are synthesized into an
answer (:class:`alvis.answer.Synthesizer`) and graded by an LLM judge
(:class:`alvis.judge.AnswerJudge`) for faithfulness and relevancy — see
:mod:`alvis.judge`. The harness still needs no LLM/API key by default;
``rerank``/``llm``+``judge`` are the opt-in exceptions.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from alvis.answer import Answer, Synthesizer, citation_answer
from alvis.config import ConfigError
from alvis.core.models import SearchHit
from alvis.index.base import Indexer
from alvis.judge import AnswerJudge, JudgeScore
from alvis.pipeline.runner import ConfigLike, query_async
from alvis.rerank import LLMReranker
from alvis.sources.base import SourceError


class EvalCase(BaseModel):
    """One retrieval expectation: a query and the document it should surface.

    ``principals``, if set, overrides the harness-level ``principals`` for
    just this case — useful for asserting ACL enforcement itself (e.g. "as
    user A, this query hits doc X; as user B, it doesn't").
    """

    model_config = ConfigDict(frozen=True)

    query: str
    expected_source_uri: str
    principals: tuple[str, ...] | None = None


@dataclass(frozen=True)
class EvalCaseResult:
    """Outcome of running one :class:`EvalCase` against the index."""

    case: EvalCase
    hits: tuple[SearchHit, ...]
    rank: int | None
    """1-based rank of the first hit whose ``source_uri`` matches, else ``None``."""
    answer: Answer | None = None
    """Synthesized answer over ``hits``, only set when ``evaluate`` was given
    both ``llm`` and ``judge``."""
    judge: JudgeScore | None = None
    """LLM-judge verdict on ``answer``, only set alongside ``answer``."""

    @property
    def hit(self) -> bool:
        """Whether the expected document was retrieved within ``top_k``."""
        return self.rank is not None


@dataclass(frozen=True)
class EvalReport:
    """Aggregate retrieval-quality metrics over a set of cases."""

    results: tuple[EvalCaseResult, ...]

    @property
    def hit_rate(self) -> float:
        """Fraction of cases where the expected document was retrieved at all."""
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.hit) / len(self.results)

    @property
    def mrr(self) -> float:
        """Mean reciprocal rank — 0 for a miss, ``1/rank`` for a hit."""
        if not self.results:
            return 0.0
        return sum(1.0 / r.rank if r.rank else 0.0 for r in self.results) / len(self.results)

    @property
    def misses(self) -> tuple[EvalCaseResult, ...]:
        """Cases where the expected document did not come back at all."""
        return tuple(r for r in self.results if not r.hit)

    @property
    def judged(self) -> tuple[EvalCaseResult, ...]:
        """Cases that were also answer-quality judged (``judge`` was given)."""
        return tuple(r for r in self.results if r.judge is not None)

    @property
    def mean_faithfulness(self) -> float:
        """Mean judge faithfulness over :attr:`judged` cases (0.0 if none)."""
        judged = self.judged
        if not judged:
            return 0.0
        return sum(r.judge.faithfulness for r in judged if r.judge) / len(judged)

    @property
    def mean_relevancy(self) -> float:
        """Mean judge relevancy over :attr:`judged` cases (0.0 if none)."""
        judged = self.judged
        if not judged:
            return 0.0
        return sum(r.judge.relevancy for r in judged if r.judge) / len(judged)


def load_cases(path: str | Path) -> list[EvalCase]:
    """Load eval cases from a YAML file: a list of ``{query, expected_source_uri}``.

    Example::

        - query: "how do I install alvis?"
          expected_source_uri: "install.md"
        - query: "what chunk strategies exist?"
          expected_source_uri: "chunking.md"
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{path}: expected a non-empty YAML list of eval cases")
    try:
        return [EvalCase.model_validate(item) for item in raw]
    except Exception as exc:  # pydantic ValidationError, wrapped for a stable surface
        raise ConfigError(f"{path}: invalid eval case — {exc}") from exc


async def evaluate_async(
    config: ConfigLike,
    cases: Sequence[EvalCase],
    *,
    top_k: int = 5,
    indexer: Indexer | None = None,
    hybrid: bool = False,
    rerank: LLMReranker | None = None,
    principals: Sequence[str] | None = None,
    llm: Synthesizer | None = None,
    judge: AnswerJudge | None = None,
) -> EvalReport:
    """Run every case's query against the configured index and score retrieval.

    A case counts as a hit when ``expected_source_uri`` is a substring of some
    returned hit's ``source_uri`` within the top ``top_k`` results. ``hybrid``
    and ``rerank`` are passed straight through to
    :func:`alvis.pipeline.runner.query_async` — use them to compare
    dense-only vs. dense+BM25 vs. reranked hit rate on the same cases. The
    harness needs no LLM/API key by default; passing ``rerank`` is one way
    to opt into one. ``principals`` is the default identity for every
    case; a case's own ``principals`` (if set) overrides it.

    Passing both ``llm`` and ``judge`` additionally synthesizes an answer
    over each case's hits and scores it for faithfulness/relevancy (see
    :mod:`alvis.judge`) — a second, separate axis from retrieval hit rate;
    giving only one of the two leaves answer-quality scoring off (retrieval
    is still scored either way).
    """
    results: list[EvalCaseResult] = []
    for case in cases:
        case_principals = case.principals if case.principals is not None else principals
        hits = await query_async(
            config,
            case.query,
            top_k=top_k,
            indexer=indexer,
            hybrid=hybrid,
            rerank=rerank,
            principals=case_principals,
        )
        rank = next(
            (
                i
                for i, hit in enumerate(hits, start=1)
                if case.expected_source_uri in hit.source_uri
            ),
            None,
        )
        answer_obj: Answer | None = None
        judge_score: JudgeScore | None = None
        if llm is not None and judge is not None:
            try:
                answer_obj = await llm.answer(case.query, hits)
            except SourceError:
                answer_obj = citation_answer(case.query, hits, reason="LLM call failed")
            judge_score = await judge.score(case.query, answer_obj.text, hits)
        results.append(
            EvalCaseResult(
                case=case, hits=tuple(hits), rank=rank, answer=answer_obj, judge=judge_score
            )
        )
    return EvalReport(results=tuple(results))


def evaluate(
    config: ConfigLike,
    cases: Sequence[EvalCase],
    *,
    top_k: int = 5,
    indexer: Indexer | None = None,
    hybrid: bool = False,
    rerank: LLMReranker | None = None,
    principals: Sequence[str] | None = None,
    llm: Synthesizer | None = None,
    judge: AnswerJudge | None = None,
) -> EvalReport:
    """Synchronous variant of :func:`evaluate_async`."""
    return asyncio.run(
        evaluate_async(
            config,
            cases,
            top_k=top_k,
            indexer=indexer,
            hybrid=hybrid,
            rerank=rerank,
            principals=principals,
            llm=llm,
            judge=judge,
        )
    )


__all__ = [
    "EvalCase",
    "EvalCaseResult",
    "EvalReport",
    "evaluate",
    "evaluate_async",
    "load_cases",
]
