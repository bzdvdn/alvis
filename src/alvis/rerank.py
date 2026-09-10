"""LLM-based reranking — reorder retrieved candidates by relevance.

A cross-encoder reranker needs a local model; this project ships zero ML
dependencies (embedding is an HTTP abstraction, not a local model — see
CONSTITUTION.md). ``LLMReranker`` instead asks an OpenAI-compatible chat
endpoint to reorder numbered candidates by relevance to the query — the
same contract :class:`alvis.answer.Synthesizer` already speaks, so it works
with OpenAI, Azure, or any local server exposing that API.

Fails soft: a network error or an unparseable response logs a warning and
falls back to the original (dense or hybrid-fused) order, truncated to
``top_k`` — reranking never turns a working query into a failed one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from alvis.core.models import SearchHit
from alvis.observability import LOG
from alvis.sources.http import HttpClient

_SYSTEM_PROMPT = (
    "You rank excerpts by relevance to a query. Given a query and numbered "
    "excerpts, respond with ONLY a JSON array of the excerpt numbers, "
    "ordered from most to least relevant to the query. Include every "
    "number exactly once. No prose, no markdown fences — the array alone."
)

_MAX_EXCERPT_CHARS = 2000
"""Per-excerpt cap on the text sent to the reranking LLM.

Candidates are already over-fetched (``top_k * _CANDIDATE_FACTOR`` in
``pipeline.runner``) and chunk text can run long (a whole PDF section) —
uncapped, a handful of candidates can exceed the chat model's context
window. Ranking only needs enough of each excerpt to judge relevance to
the query, not the full text, so this is a relevance-judgment budget, not
a citation source — the *original* untruncated ``SearchHit`` is still what
gets returned and, downstream, synthesized into an answer."""


class LLMReranker:
    """Reorders :class:`SearchHit` candidates via a chat-completions endpoint.

    Config mirrors :class:`alvis.answer.Synthesizer`: ``base_url`` (any
    OpenAI-compatible server), ``model``, optional ``api_token_env``.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_token_env: str | None = None,
        timeout: float = 60.0,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        transport: Any = None,
        temperature: float = 0.0,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.client = HttpClient(
            base_url=base_url,
            api_token_env=api_token_env,
            timeout=timeout,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
            transport=transport,
        )

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self.client.aclose()

    async def rerank(
        self,
        query: str,
        hits: Sequence[SearchHit],
        *,
        top_k: int,
    ) -> list[SearchHit]:
        """Return up to ``top_k`` hits from ``hits``, reordered by relevance.

        Never raises: a failed call or an unparseable response logs a
        warning and returns ``hits[:top_k]`` in the original order instead.
        """
        if not hits:
            return []
        try:
            order = await self._rank_order(query, hits)
        except Exception:
            LOG.warning("rerank failed; keeping original order", exc_info=True)
            return list(hits[:top_k])
        return [hits[i] for i in order][:top_k]

    async def _rank_order(self, query: str, hits: Sequence[SearchHit]) -> list[int]:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _prompt(query, hits)},
            ],
        }
        response = await self.client.request(
            "POST",
            "/chat/completions",
            payload=payload,
            ok_status=(200,),
        )
        content = str(response["choices"][0]["message"]["content"])
        return _parse_order(content, len(hits))


def _truncate(text: str, limit: int = _MAX_EXCERPT_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _prompt(query: str, hits: Sequence[SearchHit]) -> str:
    excerpts = "\n\n".join(
        f"[{index}]\n{_truncate(hit.text)}" for index, hit in enumerate(hits, start=1)
    )
    return f"Query: {query}\n\nExcerpts:\n{excerpts}"


def _parse_order(content: str, count: int) -> list[int]:
    """Parse a ``[3, 1, 2, ...]`` response into 0-based indices.

    Raises ``ValueError`` (caught by :meth:`LLMReranker.rerank`) if the
    response has no JSON array, or the array isn't a permutation of
    ``1..count`` — a model that hallucinates numbers must not silently drop
    or duplicate hits.
    """
    match = re.search(r"\[[\s\d,]*\]", content)
    if not match:
        raise ValueError(f"no JSON array found in rerank response: {content!r}")
    numbers = json.loads(match.group(0))
    indices = [int(number) - 1 for number in numbers]
    if sorted(indices) != list(range(count)):
        raise ValueError(f"rerank response is not a permutation of 1..{count}: {numbers!r}")
    return indices


__all__ = ["LLMReranker"]
