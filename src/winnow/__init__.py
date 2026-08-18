"""Winnow — no-code knowledge ingestion engine."""

from winnow import dsl
from winnow.pipeline.engine import PipelineEngine, PipelineResult
from winnow.pipeline.runner import run, run_async, run_many, run_many_async

__version__ = "0.1.0"

__all__ = [
    "PipelineEngine",
    "PipelineResult",
    "dsl",
    "run",
    "run_async",
    "run_many",
    "run_many_async",
]