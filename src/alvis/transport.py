"""Shared transport helpers (retry status codes, exponential backoff)."""

from __future__ import annotations

import random

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def backoff_delay(attempt: int, retry_backoff: float) -> float:
    """Exponential delay (with jitter) before retry ``attempt`` (0-based)."""
    base = retry_backoff * float(2**attempt)
    return base + random.uniform(0.0, retry_backoff)