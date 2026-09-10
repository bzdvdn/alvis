"""Bounded-concurrency fan-out — a small, dependency-free helper.

Plain ``asyncio.gather`` over many coroutines fans out unboundedly; wrapping
each with a shared ``asyncio.Semaphore`` caps how many run at once. Used by
sources that fetch many individual documents from one host (GitHub, GitLab,
Confluence, S3, static URLs) so a large corpus dispatches several requests
concurrently instead of one at a time, without hammering the host with as
many concurrent requests as there are documents.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

_T = TypeVar("_T")
_R = TypeVar("_R")


async def gather_bounded(
    items: Sequence[_T],
    fn: Callable[[_T], Awaitable[_R]],
    *,
    max_concurrency: int,
) -> list[_R]:
    """Apply ``fn`` to every item, at most ``max_concurrency`` at once.

    Preserves input order in the returned list regardless of completion
    order. If one call raises, the exception propagates after the others
    in flight finish (``asyncio.gather`` default semantics).
    """
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be >= 1")
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _one(item: _T) -> _R:
        async with semaphore:
            return await fn(item)

    return list(await asyncio.gather(*(_one(item) for item in items)))


__all__ = ["gather_bounded"]
