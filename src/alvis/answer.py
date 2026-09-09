"""Response synthesis with citations — answering, not just retrieval.

Retrieval returns :class:`SearchHit` chunks; this module turns them into a
cited answer. Two modes:

- :class:`Synthesizer` — an LLM client for the OpenAI-compatible
  ``POST {base_url}/chat/completions`` contract (same shape as the
  ``openai`` embedder: OpenAI, Azure, local servers).
- :func:`citation_answer` — a no-LLM fallback that numbers the top excerpts
  so ``--answer`` works even with no API key configured.

The answer's citations are derived from the ``[N]`` markers the model emits,
so only the excerpts actually used end up cited.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from alvis.core.models import SearchHit
from alvis.sources.http import HttpClient

SYSTEM_PROMPT = (
    "You answer questions using only the provided excerpts. "
    "After each sentence that draws on an excerpt, cite it as [N]. "
    "If no excerpt is relevant, say that you don't know. Be concise."
)


class Citation(BaseModel):
    """One excerpt the answer references with a ``[N]`` marker."""

    model_config = ConfigDict(frozen=True)

    index: int
    source_uri: str
    score: float
    snippet: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class Answer(BaseModel):
    """A synthesized answer plus the citations it references."""

    model_config = ConfigDict(frozen=True)

    text: str
    citations: tuple[Citation, ...] = Field(default_factory=tuple)


class Synthesizer:
    """Answers questions over retrieval hits via a chat-completions endpoint.

    Config mirrors the ``openai`` embedder: ``base_url`` (any OpenAI-
    compatible server), ``model``, optional ``api_token_env``. Retries and
    retries/backoff reuse :class:`HttpClient`, so behaviour matches sources.
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
        self.signature = f"openai-chat:{model}"

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self.client.aclose()

    async def answer(self, question: str, hits: Sequence[SearchHit]) -> Answer:
        """Synthesize a cited answer over the given retrieval hits."""
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(question, hits)},
            ],
        }
        response = await self.client.request(
            "POST",
            "/chat/completions",
            payload=payload,
            ok_status=(200,),
        )
        content = str(response["choices"][0]["message"]["content"])
        return build_answer(content, hits)


def build_answer(text: str, hits: Sequence[SearchHit]) -> Answer:
    """Parse ``[N]`` markers out of ``text`` and attach the cited excerpts."""
    cited = sorted(
        {int(m) for m in re.findall(r"\[(\d{1,3})\]", text) if 1 <= int(m) <= len(hits)}
    )
    return Answer(
        text=text,
        citations=tuple(_citation(index, hits[index - 1]) for index in cited),
    )


def citation_answer(
    question: str,
    hits: Sequence[SearchHit],
    *,
    reason: str = "No LLM configured",
) -> Answer:
    """Offline fallback: number the top excerpts without calling an LLM."""
    lines = [f"{reason}; here are the most relevant excerpts.", ""]
    for index, hit in enumerate(hits, start=1):
        lines.append(f"[{index}] {hit.text.strip()}")
        lines.append(f"    @ {hit.source_uri}")
    return Answer(
        text="\n".join(lines),
        citations=tuple(_citation(index, hit) for index, hit in enumerate(hits, start=1)),
    )


def _user_prompt(question: str, hits: Sequence[SearchHit]) -> str:
    excerpts = "\n\n".join(
        f"[{index}]\n{hit.text.strip()}\n(Source: {hit.source_uri})"
        for index, hit in enumerate(hits, start=1)
    )
    return (
        f"Answer the question using only the excerpts below. Cite each "
        f"excerpt you use as [N] immediately after the sentence that uses it.\n\n"
        f"Question: {question}\n\n"
        f"Excerpts:\n{excerpts}"
    )


def _citation(index: int, hit: SearchHit) -> Citation:
    return Citation(
        index=index,
        source_uri=hit.source_uri,
        score=hit.score,
        snippet=" ".join(hit.text.split())[:180],
        metadata=hit.metadata,
    )


__all__ = ["Answer", "Citation", "Synthesizer", "build_answer", "citation_answer"]