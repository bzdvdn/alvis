"""Registry of built-in adapter types supported by Winnow.

v0.1: structural reference only — `winnow validate` checks membership here.
Plugin registration (v1.1) will extend these sets dynamically.
"""

from __future__ import annotations

KNOWN_SOURCES: set[str] = {"fs", "confluence", "gitlab", "s3"}
KNOWN_EXTRACT_STRATEGIES: set[str] = {"auto"}
KNOWN_CHUNK_STRATEGIES: set[str] = {"auto"}
KNOWN_EMBEDDERS: set[str] = {"default"}
KNOWN_INDEXES: set[str] = {"memory", "qdrant", "pgvector"}


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

    if source not in KNOWN_SOURCES:
        problems.append(f"unsupported source type: {source!r} (known: {sorted(KNOWN_SOURCES)})")
    if extract not in KNOWN_EXTRACT_STRATEGIES:
        problems.append(
            f"unsupported extract strategy: {extract!r} (known: {sorted(KNOWN_EXTRACT_STRATEGIES)})"
        )
    if chunk not in KNOWN_CHUNK_STRATEGIES:
        problems.append(
            f"unsupported chunk strategy: {chunk!r} (known: {sorted(KNOWN_CHUNK_STRATEGIES)})"
        )
    if embed not in KNOWN_EMBEDDERS:
        problems.append(f"unsupported embed type: {embed!r} (known: {sorted(KNOWN_EMBEDDERS)})")
    if index is not None and index not in KNOWN_INDEXES:
        problems.append(f"unsupported index type: {index!r} (known: {sorted(KNOWN_INDEXES)})")

    return problems