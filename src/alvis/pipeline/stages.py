"""Pipeline stages — one class per phase, orchestrated by the engine.

A :class:`RunContext` carries the built adapters and the mutable accounting
state once, so a stage owns only what it touches. The engine runs the stages
in a fixed order and times each one with a ``pipeline_stage_seconds
stage=<name>`` histogram sample (FetchStage additionally times its ``list``
sub-step). Each stage is independently testable and swappable — the contract
is deliberately small: ``applicable(ctx)`` gates participation and
``run(ctx)`` performs the phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

import alvis.observability as ob
from alvis.chunk.auto import Chunker
from alvis.config import PipelineConfig
from alvis.core.models import Artifact, Chunk, Document, DocumentMeta, content_hash
from alvis.docstore import DocEntry, DocStore
from alvis.embed.base import Embedder
from alvis.errors import PipelineError
from alvis.extract.base import Extractor
from alvis.index.base import Indexer
from alvis.sources.base import ListingSource, Source, SourceError


@dataclass
class RunContext:
    """Adapters and accounting state shared across the stages of one run."""

    config: PipelineConfig
    docstore: DocStore | None
    source_id: str
    signature: str | None
    source: Source
    extractor: Extractor
    chunker: Chunker
    embedder: Embedder
    indexer: Indexer | None
    metric_labels: dict[str, str]

    use_listing: bool = False
    uri_by_meta: dict[str, DocumentMeta] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    current: dict[str, str] = field(default_factory=dict)
    entries: dict[str, DocEntry] = field(default_factory=dict)
    pending: list[tuple[Chunk, str]] = field(default_factory=list)
    vectors: list[list[float]] = field(default_factory=list)
    documents: int = 0
    indexed: int = 0
    changed: int = 0
    skipped: int = 0
    deleted: int = 0

    @property
    def embed_cache_hits(self) -> int:
        return int(getattr(self.embedder, "cache_hits", 0))

    @property
    def embed_cache_misses(self) -> int:
        return int(getattr(self.embedder, "cache_misses", 0))

    def stage_labels(self, stage: str) -> dict[str, str]:
        """Labels for a stage histogram, e.g. ``stage=fetch`` plus source/index."""
        return {**self.metric_labels, "stage": stage}


class BaseStage:
    """A runnable phase with a stable metric ``stage`` label."""

    name: str = ""

    def applicable(self, ctx: RunContext) -> bool:
        """Whether this run needs the stage (default: always)."""
        return True

    async def run(self, ctx: RunContext) -> None:
        """Perform the phase against ``ctx``."""
        raise NotImplementedError


def _supports_listing(source: Source) -> bool:
    return getattr(source, "list_documents", None) is not None


class FetchStage(BaseStage):
    """Fingerprint the source list, decide what changed, and fetch bodies.

    With a DocStore and a listing-capable source, unchanged documents are
    skipped from their listing data alone and only the changed bodies are
    downloaded. Otherwise the source's full ``fetch()`` is used.
    """

    name = "fetch"

    async def run(self, ctx: RunContext) -> None:
        try:
            if (
                ctx.docstore is not None
                and ctx.signature is not None
                and _supports_listing(ctx.source)
            ):
                ctx.use_listing = True
                listing = cast(ListingSource, ctx.source)
                with ob.timer("pipeline_stage_seconds", labels=ctx.stage_labels("list")):
                    metas = await listing.list_documents()
                ctx.uri_by_meta = {meta.uri: meta for meta in metas}
                wanted: set[str] = set()
                for meta in metas:
                    ctx.documents += 1
                    stored = ctx.docstore.entry(ctx.source_id, ctx.signature, meta.uri)
                    if (
                        stored is not None
                        and stored.listing is not None
                        and stored.listing == meta.fingerprint
                    ):
                        ctx.skipped += 1
                        ctx.current[meta.uri] = stored.content
                        ctx.entries[meta.uri] = stored
                    else:
                        ctx.changed += 1
                        wanted.add(meta.uri)
                ctx.artifacts = await listing.fetch(uris=wanted) if wanted else []
            else:
                ctx.artifacts = await ctx.source.fetch()
        except SourceError as exc:
            raise PipelineError(
                f"source '{ctx.config.source.type}' failed: {exc}"
            ) from exc


class ExtractStage(BaseStage):
    """Extract + chunk every artifact that reached the engine.

    Without listing, this stage also performs the content-hash delta decision
    per artifact. Artifact metadata is merged into every chunk's metadata so
    downstream filters can rely on source fields.
    """

    name = "extract"

    async def run(self, ctx: RunContext) -> None:
        for artifact in ctx.artifacts:
            artifact_hash = content_hash(artifact)
            ctx.current[artifact.uri] = artifact_hash
            if not ctx.use_listing:
                ctx.documents += 1
                stored = (
                    ctx.docstore.entry(ctx.source_id, ctx.signature, artifact.uri)
                    if ctx.docstore is not None and ctx.signature is not None
                    else None
                )
                if stored is not None and stored.content == artifact_hash:
                    ctx.skipped += 1
                    ctx.entries[artifact.uri] = DocEntry(content=artifact_hash)
                    continue
                ctx.changed += 1
            listed = ctx.uri_by_meta.get(artifact.uri)
            ctx.entries[artifact.uri] = DocEntry(
                content=artifact_hash,
                listing=listed.fingerprint if listed is not None else None,
            )
            try:
                document: Document = await ctx.extractor.extract(artifact)
                doc_chunks = 0
                for chunk in await ctx.chunker.chunk(document):
                    ctx.indexed += 1
                    doc_chunks += 1
                    if ctx.indexer is not None:
                        enriched = chunk.model_copy(
                            update={"metadata": {**artifact.metadata, **chunk.metadata}}
                        )
                        ctx.pending.append((enriched, artifact_hash))
                ob.LOG.debug(
                    "artifact.processed",
                    extra={
                        "uri": artifact.uri,
                        "chunks": doc_chunks,
                        "bytes": len(artifact.data),
                    },
                )
            except (SourceError, ValueError) as exc:
                raise PipelineError(
                    f"failed on artifact {artifact.uri}: {exc}"
                ) from exc


class EmbedStage(BaseStage):
    """Embed the run's pending chunks in a single batch."""

    name = "embed"

    def applicable(self, ctx: RunContext) -> bool:
        return ctx.indexer is not None and bool(ctx.pending)

    async def run(self, ctx: RunContext) -> None:
        chunks = [chunk for chunk, _ in ctx.pending]
        try:
            ctx.vectors = await ctx.embedder.embed_batch(chunks)
            ob.LOG.debug("embed.batch", extra={**ctx.metric_labels, "chunks": len(chunks)})
        except (SourceError, ValueError) as exc:
            raise PipelineError(f"embedding failed: {exc}") from exc
        finally:
            closer = getattr(ctx.embedder, "close", None)
            if callable(closer):
                closer()


class UpsertStage(BaseStage):
    """Write the run's vectors to the index."""

    name = "upsert"

    def applicable(self, ctx: RunContext) -> bool:
        return ctx.indexer is not None and bool(ctx.pending)

    async def run(self, ctx: RunContext) -> None:
        indexer = ctx.indexer
        assert indexer is not None, "UpsertStage ran without an indexer"
        for (chunk, artifact_hash), vector in zip(ctx.pending, ctx.vectors, strict=True):
            await indexer.upsert(
                chunk,
                vector,
                source_id=ctx.source_id,
                artifact_hash=artifact_hash,
            )


class ReconcileStage(BaseStage):
    """Prune index points that no longer correspond to a listed artifact."""

    name = "reconcile"

    def applicable(self, ctx: RunContext) -> bool:
        return ctx.indexer is not None

    async def run(self, ctx: RunContext) -> None:
        indexer = ctx.indexer
        assert indexer is not None, "ReconcileStage ran without an indexer"
        await indexer.reconcile(ctx.source_id, ctx.current)


class CommitStage(BaseStage):
    """Persist DocStore state only after every earlier stage succeeded.

    This must run last: it is what makes incremental state durable, and it
    must never record a run that crashed mid-way.
    """

    name = "commit"

    def applicable(self, ctx: RunContext) -> bool:
        return ctx.docstore is not None and ctx.signature is not None

    async def run(self, ctx: RunContext) -> None:
        docstore = ctx.docstore
        assert docstore is not None, "CommitStage ran without a DocStore"
        assert ctx.signature is not None
        ctx.deleted = docstore.commit(ctx.source_id, ctx.signature, ctx.entries)
        docstore.save()


STAGES: tuple[BaseStage, ...] = (
    FetchStage(),
    ExtractStage(),
    EmbedStage(),
    UpsertStage(),
    ReconcileStage(),
    CommitStage(),
)