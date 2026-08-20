"""Alvis — no-code knowledge ingestion engine."""

from alvis import dsl
from alvis.answer import Answer, Citation, Synthesizer, citation_answer
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

__version__ = "0.6.0"

__all__ = [
    "Answer",
    "Citation",
    "PipelineEngine",
    "PipelineResult",
    "Synthesizer",
    "answer",
    "answer_async",
    "citation_answer",
    "describe",
    "dsl",
    "query",
    "query_async",
    "run",
    "run_async",
    "run_many",
    "run_many_async",
    "watch_async",
]
