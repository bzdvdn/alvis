"""BM25 keyword scoring — the lexical half of hybrid search.

Dense vectors (especially the deterministic ``default`` embedder) can miss
what a plain keyword match catches trivially — an exact ID, an acronym, a
rare term diluted by everything else in a chunk. BM25 here is the classic
Robertson/Sparck-Jones ranking function, computed in pure Python over the
backend's own stored text. No external dependency, same philosophy as the
``default`` embedder: a correct, unglamorous baseline that works everywhere.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase word/digit tokens — same scheme as the ``default`` embedder."""
    return _TOKEN_RE.findall(text.lower())


def bm25_scores(
    query_tokens: Sequence[str],
    documents: Sequence[Sequence[str]],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    """BM25 score of ``query_tokens`` against each already-tokenized document.

    Standard formula: for each query term, ``idf(t) * f(t,D) * (k1+1) /
    (f(t,D) + k1 * (1 - b + b * |D| / avgdl))``, summed over query terms.
    Returns one score per document, same order as ``documents``; higher is
    more relevant. Scores are unbounded (not cosine-like), which is fine —
    callers rank or fuse by rank, not by comparing raw magnitudes across
    scoring methods.
    """
    n_docs = len(documents)
    if n_docs == 0 or not query_tokens:
        return [0.0] * n_docs

    doc_freqs = [Counter(doc) for doc in documents]
    doc_lens = [len(doc) for doc in documents]
    avg_len = (sum(doc_lens) / n_docs) if n_docs else 0.0

    unique_terms = set(query_tokens)
    doc_freq = {
        term: sum(1 for freqs in doc_freqs if term in freqs) for term in unique_terms
    }
    idf = {
        term: math.log((n_docs - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5) + 1.0)
        for term in unique_terms
    }

    scores: list[float] = []
    for freqs, length in zip(doc_freqs, doc_lens, strict=True):
        score = 0.0
        norm_len = length / avg_len if avg_len else 1.0
        for term in unique_terms:
            frequency = freqs.get(term, 0)
            if frequency == 0:
                continue
            denominator = frequency + k1 * (1 - b + b * norm_len)
            score += idf[term] * (frequency * (k1 + 1)) / denominator
        scores.append(score)
    return scores


__all__ = ["bm25_scores", "tokenize"]
