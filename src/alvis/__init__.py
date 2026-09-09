"""Alvis — no-code knowledge ingestion engine."""

from alvis import dsl
from alvis.answer import Answer, Citation, Synthesizer, citation_answer
from alvis.evaluation import (
    EvalCase,
    EvalCaseResult,
    EvalReport,
    evaluate,
    evaluate_async,
    load_cases,
)
from alvis.pipeline.engine import PipelineEngine, PipelineResult
from alvis.pipeline.runner import (
    answer,
    answer_async,
    describe,
    query,
    query_async,
    run,
    run_async,
    run_many,
    run_many_async,
    watch_async,
)
from alvis.rerank import LLMReranker

__version__ = "1.0.0rc2"

__all__ = [
    "Answer",
    "Citation",
    "EvalCase",
    "EvalCaseResult",
    "EvalReport",
    "LLMReranker",
    "PipelineEngine",
    "PipelineResult",
    "Synthesizer",
    "answer",
    "answer_async",
    "citation_answer",
    "describe",
    "dsl",
    "evaluate",
    "evaluate_async",
    "load_cases",
    "query",
    "query_async",
    "run",
    "run_async",
    "run_many",
    "run_many_async",
    "watch_async",
]
