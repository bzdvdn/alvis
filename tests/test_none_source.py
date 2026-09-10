"""NoneSource — a source with no documents, for query-only pipelines."""

from __future__ import annotations

from alvis import dsl, run_async
from alvis.index import MemoryIndex
from alvis.pipeline.runner import query_async
from alvis.sources.none import NoneSource


async def test_none_source_lists_and_fetches_nothing() -> None:
    source = NoneSource()
    assert await source.list_documents() == []
    assert await source.fetch() == []
    assert await source.fetch(uris={"anything"}) == []


def test_dsl_none_builds_a_none_type_config() -> None:
    cfg = dsl.none()
    assert cfg.type == "none"
    assert cfg.config == {}


async def test_run_async_on_a_none_pipeline_is_a_harmless_noop() -> None:
    """Running a none-source pipeline (e.g. by mistake) must not error or
    touch the index — it just ingests zero documents."""
    config = dsl.pipeline(dsl.none(), index=dsl.memory())
    result = await run_async(config)
    assert result.documents_ingested == 0
    assert result.chunks_indexed == 0


async def test_query_async_works_against_a_none_pipeline_with_a_real_indexer() -> None:
    """The actual motivating use case: build a query-only PipelineConfig with
    dsl.none() instead of a throwaway real source, and query a
    separately-populated indexer through it."""
    from alvis.core.models import Chunk
    from alvis.embed.hash import HashEmbedder

    indexer = MemoryIndex()
    query_config = dsl.pipeline(dsl.none(), index=dsl.memory())
    chunk = Chunk(text="install alvis via pip", source_uri="u/1")
    vector = await HashEmbedder().embed(chunk)
    await indexer.upsert(chunk, vector, source_id="s", artifact_hash="h")

    hits = await query_async(query_config, "install alvis via pip", indexer=indexer, top_k=3)

    assert hits and hits[0].source_uri == "u/1"


def test_none_registered_as_known_source() -> None:
    from alvis.registry import known_sources

    assert "none" in known_sources()


def test_factories_build_source_returns_none_source() -> None:
    from alvis.config.models import SourceConfig
    from alvis.factories import build_source

    source = build_source(SourceConfig(type="none", config={}))
    assert isinstance(source, NoneSource)
