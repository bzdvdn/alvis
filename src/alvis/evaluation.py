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

This proves *retrieval* quality (did the right chunk come back), not
*answer* quality (faithfulness/relevancy over an LLM's response) — that is
a separate, LLM-judge-based harness for later; this one runs in CI with no
API key, same as the rest of the test suite.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from alvis.config import ConfigError
from alvis.core.models import SearchHit
from alvis.index.base import Indexer
from alvis.pipeline.runner import ConfigLike, query_async
from alvis.rerank import LLMReranker


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
) -> EvalReport:
    """Run every case's query against the configured index and score retrieval.

    A case counts as a hit when ``expected_source_uri`` is a substring of some
    returned hit's ``source_uri`` within the top ``top_k`` results. ``hybrid``
    and ``rerank`` are passed straight through to
    :func:`alvis.pipeline.runner.query_async` — use them to compare
    dense-only vs. dense+BM25 vs. reranked hit rate on the same cases. The
    harness needs no LLM/API key by default; passing ``rerank`` is the one
    way to opt into one. ``principals`` is the default identity for every
    case; a case's own ``principals`` (if set) overrides it.
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
        results.append(EvalCaseResult(case=case, hits=tuple(hits), rank=rank))
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
