"""Exception types shared across pipeline stages."""

from __future__ import annotations


class PipelineError(Exception):
    """Raised when a stage fails during a pipeline run, with stage context."""