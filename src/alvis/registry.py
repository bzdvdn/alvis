"""Registry of adapter types supported by Alvis.

Built-in types are the sets below; plugin types (roadmap v1.1) extend them
dynamically via the plugin registry. ``check_pipeline_supported`` is the one
place all validation goes through — CLI ``validate``/``--dry-run`` and the
engine's pre-run check — so a plugin type is accepted the moment it is
installed and discovered.
"""

from __future__ import annotations


def _plugin_types(kind: str) -> set[str]:
    from alvis.plugin import registry

    return registry().known_types(kind)


def known_sources() -> set[str]:
    return KNOWN_SOURCES | _plugin_types("source")


def known_extract_strategies() -> set[str]:
    return KNOWN_EXTRACT_STRATEGIES | _plugin_types("extractor")


def known_chunk_strategies() -> set[str]:
    return KNOWN_CHUNK_STRATEGIES | _plugin_types("chunker")


def known_embedders() -> set[str]:
    return KNOWN_EMBEDDERS | _plugin_types("embedder")


def known_indexes() -> set[str]:
    return KNOWN_INDEXES | _plugin_types("indexer")


KNOWN_SOURCES: set[str] = {"fs", "confluence", "github", "gitlab", "s3", "static_url"}
KNOWN_EXTRACT_STRATEGIES: set[str] = {"auto"}
KNOWN_CHUNK_STRATEGIES: set[str] = {"auto", "sections", "size"}
KNOWN_EMBEDDERS: set[str] = {"default", "openai"}
KNOWN_INDEXES: set[str] = {"memory", "qdrant", "pgvector", "sqlite"}


def check_pipeline_supported(
    *,
    source: str,
    extract: str,
    chunk: str,
    embed: str,
    index: str | None,
) -> list[str]:
    """Return human-readable messages for unsupported adapter types.

    A ``None`` index (no index stage configured) is always accepted.
    """
    problems: list[str] = []

    if source not in known_sources():
        problems.append(f"unsupported source type: {source!r} (known: {sorted(known_sources())})")
    if extract not in known_extract_strategies():
        problems.append(
            f"unsupported extract strategy: {extract!r} "
            f"(known: {sorted(known_extract_strategies())})"
        )
    if chunk not in known_chunk_strategies():
        problems.append(
            f"unsupported chunk strategy: {chunk!r} (known: {sorted(known_chunk_strategies())})"
        )
    if embed not in known_embedders():
        problems.append(f"unsupported embed type: {embed!r} (known: {sorted(known_embedders())})")
    if index is not None and index not in known_indexes():
        problems.append(f"unsupported index type: {index!r} (known: {sorted(known_indexes())})")

    return problems