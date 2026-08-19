"""Per-source document state for incremental ingestion.

Idempotent re-runs already avoid duplicating points; incremental ingestion
adds *skipping*: documents whose fingerprint is unchanged are not re-
extracted, re-chunked, or re-indexed.

Two fingerprints are tracked per document:

- ``content`` — the sha256 of the fetched body; used for the index's
  ``reconcile`` mapping (identical to what ingestion stores per point).
- ``listing`` — a cheap fingerprint obtained *before* the body is fetched
  (S3 ETag, GitLab/GitHub blob sha, Confluence version, local file hash).
  When it matches, the body is never downloaded at all.

State is keyed by source identity **and** by a pipeline signature (extract /
chunk / embed settings): changing the pipeline invalidates stored
fingerprints instead of silently serving stale chunks. State is committed
only after a successful run, so a failed run leaves the previous state
intact. State files written before ``listing`` existed are migrated on load.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_VERSION = 2


@dataclass(frozen=True)
class DocEntry:
    """Stored fingerprints of one document from the last successful run."""

    content: str
    listing: str | None = None


class DocStore:
    """A JSON-backed map of ``source identity -> {uri -> DocEntry}``.

    One entry per source, tagged with the pipeline signature that produced
    it. A different signature replaces the entry on commit, so results are
    never served stale across config changes.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._sources: dict[str, dict[str, object]] = self._load()

    @staticmethod
    def default_path() -> Path:
        """Conventional state location (``.winnow/state.json`` in the cwd)."""
        return Path(".winnow") / "state.json"

    def entry(self, source_id: str, signature: str, uri: str) -> DocEntry | None:
        """Stored entry for ``uri``, or ``None`` if this source is new or was
        last ingested with a different pipeline signature."""
        entry = self._sources.get(source_id)
        if entry is None or entry.get("signature") != signature:
            return None
        documents = entry.get("documents")
        if not isinstance(documents, dict):
            return None
        value = documents.get(uri)
        if value is None:
            return None
        return _decode_entry(value)

    def commit(
        self,
        source_id: str,
        signature: str,
        current: Mapping[str, DocEntry],
    ) -> int:
        """Replace this source's state with the latest run's entries.

        ``current`` maps ``uri -> DocEntry`` for the documents present in
        this run. Returns how many previously known documents disappeared.
        """
        previous = self._sources.get(source_id, {}).get("documents", {})
        if not isinstance(previous, dict):
            previous = {}
        deleted = sum(1 for uri in previous if uri not in current)
        self._sources[source_id] = {
            "signature": signature,
            "documents": {
                uri: {
                    "content": entry.content,
                    "listing": entry.listing,
                }
                for uri, entry in current.items()
            },
        }
        return deleted

    def save(self) -> None:
        """Atomically persist the state to disk (replaces on success)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": _VERSION, "sources": self._sources},
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        ) + "\n"
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self.path)

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        sources = data.get("sources")
        if not isinstance(sources, dict):
            return {}
        loaded: dict[str, dict[str, object]] = {}
        for source_id, entry in sources.items():
            if not isinstance(entry, dict):
                continue
            loaded[str(source_id)] = entry
        return loaded


def _decode_entry(value: object) -> DocEntry:
    """Decode a stored entry, migrating the legacy ``str`` shape (content only)."""
    if isinstance(value, str):
        return DocEntry(content=value)
    if isinstance(value, dict):
        content = value.get("content")
        listing = value.get("listing")
        return DocEntry(
            content=str(content) if content is not None else "",
            listing=str(listing) if isinstance(listing, str) else None,
        )
    return DocEntry(content=str(value))


__all__ = ["DocEntry", "DocStore"]