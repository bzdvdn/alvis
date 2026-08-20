from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from alvis import dsl
from alvis.index import QdrantIndex
from alvis.pipeline.runner import query_async, run_async

pytestmark = pytest.mark.integration


async def test_filesystem_qdrant_incremental_query_and_reconcile(tmp_path: Path) -> None:
    """Exercise the real index through ingestion, retrieval, and deletion."""
    qdrant_url = os.environ.get("ALVIS_QDRANT_URL")
    if not qdrant_url:
        pytest.skip("ALVIS_QDRANT_URL is not configured")

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    document = corpus / "guide.md"
    document.write_text("Alvis stores the corporate knowledge guide.", encoding="utf-8")
    collection = f"alvis_ci_{uuid4().hex}"
    config = dsl.pipeline(
        source=dsl.fs(str(corpus)),
        chunk=dsl.chunk(max_tokens=100, overlap=0),
        index=dsl.qdrant(url=qdrant_url, collection=collection),
    )
    state = tmp_path / "state.json"

    first = await run_async(config, incremental=True, state_path=state)
    assert first.documents_changed == 1
    assert first.chunks_indexed >= 1

    hits = await query_async(config, "corporate knowledge guide", top_k=1)
    assert hits and "corporate knowledge" in hits[0].text

    second = await run_async(config, incremental=True, state_path=state)
    assert second.documents_changed == 0
    assert second.documents_skipped == 1
    assert second.chunks_indexed == 0

    document.unlink()
    third = await run_async(config, incremental=True, state_path=state)
    assert third.documents_deleted == 1
    assert await QdrantIndex(url=qdrant_url, collection=collection).count() == 0
