"""Reciprocal Rank Fusion — combine dense + keyword rankings into one order.

Hybrid search runs two independent rankers (a dense vector search and a
BM25 keyword search) and needs one ordering out of both. RRF is the
standard, parameter-light way to do that: it only looks at *rank*, not raw
score magnitude, so a cosine similarity and a BM25 score never need to be
put on the same scale.
"""

from __future__ import annotations

from collections.abc import Sequence

from alvis.core.models import SearchHit

_DEFAULT_K = 60
"""RRF's smoothing constant — the standard choice in the TREC literature.

Softens the boost given to rank 1 vs rank 2 (large ``k`` flattens the curve,
small ``k`` rewards top ranks more sharply); 60 is the widely-used default
and not something pipelines need to tune.
"""


def _identity(hit: SearchHit) -> tuple[str, str]:
    """Two hits are "the same" for fusion purposes if source + text match."""
    return (hit.source_uri, hit.text)


def reciprocal_rank_fusion(
    *rankings: Sequence[SearchHit],
    k: int = _DEFAULT_K,
) -> list[SearchHit]:
    """Merge several ranked hit lists into one, best-first.

    Each list contributes ``1 / (k + rank)`` (1-based rank) to a hit's fused
    score; a hit present in multiple lists accumulates points from each, so
    consensus across signals outranks a strong showing in only one. The
    returned hits are copies of whichever ranking first introduced them,
    with ``score`` replaced by the fused RRF score — a different scale than
    the cosine/BM25 score it started with, on purpose (it's a rank-fusion
    score, not a similarity).
    """
    fused: dict[tuple[str, str], float] = {}
    representative: dict[tuple[str, str], SearchHit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            key = _identity(hit)
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)
            representative.setdefault(key, hit)

    ordered = sorted(fused.items(), key=lambda item: item[1], reverse=True)
    return [representative[key].model_copy(update={"score": score}) for key, score in ordered]


__all__ = ["reciprocal_rank_fusion"]
