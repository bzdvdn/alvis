"""Bounded-concurrency fan-out helper (alvis._concurrency)."""

from __future__ import annotations

import asyncio

import pytest

from alvis._concurrency import gather_bounded


async def test_gather_bounded_preserves_order_regardless_of_completion_order() -> None:
    async def fn(delay: float) -> float:
        await asyncio.sleep(delay)
        return delay

    result = await gather_bounded([0.02, 0.0, 0.01], fn, max_concurrency=3)
    assert result == [0.02, 0.0, 0.01]


async def test_gather_bounded_caps_concurrent_in_flight() -> None:
    in_flight = 0
    peak_in_flight = 0

    async def fn(_: int) -> None:
        nonlocal in_flight, peak_in_flight
        in_flight += 1
        peak_in_flight = max(peak_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1

    await gather_bounded(list(range(8)), fn, max_concurrency=3)
    assert peak_in_flight == 3


async def test_gather_bounded_empty_items() -> None:
    async def fn(_: int) -> int:
        raise AssertionError("should never be called")

    assert await gather_bounded([], fn, max_concurrency=2) == []


def test_gather_bounded_rejects_max_concurrency_below_one() -> None:
    async def fn(_: int) -> int:
        return _

    with pytest.raises(ValueError, match="max_concurrency"):
        asyncio.run(gather_bounded([1], fn, max_concurrency=0))
