"""None source — a source that never has any documents.

Exists for pipelines built purely to *query* an already-ingested index
(``query``/``query_async``/``answer``/``answer_async``/``evaluate``/
``evaluate_async`` never read ``PipelineConfig.source`` at all — only
``run``/``run_async`` do), where ``source`` is otherwise a required field
with nothing legitimate to put there. Before this existed, that forced a
throwaway real source (``dsl.fs(".")`` is a common one) that coincidentally
works because ``"."`` almost always lists *something* — and would do
something unintended if ``run()`` were ever accidentally called on it.
``none`` makes the intent explicit and is safe either way: running it is a
harmless no-op (zero documents ingested), not an error, since "no
documents" is a legitimate state here, not a misconfiguration.
"""

from __future__ import annotations

from alvis.core.models import Artifact, DocumentMeta


class NoneSource:
    """A source with no documents — for query-only pipelines. Takes no config."""

    async def list_documents(self) -> list[DocumentMeta]:
        return []

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        return []


__all__ = ["NoneSource"]
