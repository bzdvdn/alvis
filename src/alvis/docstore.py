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
from typing import Any

_VERSION = 2
_RUN_HISTORY = 10


@dataclass(frozen=True)
class DocEntry:
    """Stored fingerprints of one document from the last successful run."""

    content: str
    listing: str | None = None


@dataclass
class RunRecord:
    """Outcome of one pipeline run, kept per source for operational visibility."""

    at: str
    ok: bool
    error: str = ""
    duration_seconds: float = 0.0
    documents: int = 0
    chunks: int = 0
    changed: int = 0
    skipped: int = 0
    deleted: int = 0
    embed_cache_hits: int = 0
    embed_cache_misses: int = 0

    @staticmethod
    def from_dict(value: dict[str, Any]) -> RunRecord:
        """Decode a stored record, tolerating fields added in later versions."""
        return RunRecord(
            at=str(value.get("at", "")),
            ok=bool(value.get("ok", False)),
            error=str(value.get("error", "")),
            duration_seconds=float(value.get("duration_seconds", 0.0)),
            documents=int(value.get("documents", 0)),
            chunks=int(value.get("chunks", 0)),
            changed=int(value.get("changed", 0)),
            skipped=int(value.get("skipped", 0)),
            deleted=int(value.get("deleted", 0)),
            embed_cache_hits=int(value.get("embed_cache_hits", 0)),
            embed_cache_misses=int(value.get("embed_cache_misses", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "ok": self.ok,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "documents": self.documents,
            "chunks": self.chunks,
            "changed": self.changed,
            "skipped": self.skipped,
            "deleted": self.deleted,
            "embed_cache_hits": self.embed_cache_hits,
            "embed_cache_misses": self.embed_cache_misses,
        }


class DocStore:
    """A JSON-backed map of ``source identity -> {uri -> DocEntry}``.

    One entry per source, tagged with the pipeline signature that produced
    it. A different signature replaces the entry on commit, so results are
    never served stale across config changes.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._sources: dict[str, dict[str, object]] = self._load()
        self._runs: dict[str, list[RunRecord]] = self._load_runs()

    @staticmethod
    def default_path() -> Path:
        """Conventional state location (``.alvis/state.json`` in the cwd)."""
        return Path(".alvis") / "state.json"

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
            {
                "version": _VERSION,
                "sources": self._sources,
                "runs": {
                    source_id: [run.to_dict() for run in history]
                    for source_id, history in self._runs.items()
                },
            },
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        ) + "\n"
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self.path)

    def record_run(self, source_id: str, record: RunRecord) -> None:
        """Record one run outcome for ``source_id`` and persist it.

        Keeps a bounded history per source (newest first). Persisting here
        means failures are visible to ``alvis status`` even when the run
        itself crashed — the ledger is written outside the run's success path.
        """
        history = self._runs.setdefault(source_id, [])
        history.insert(0, record)
        del history[_RUN_HISTORY:]
        self.save()

    def runs(self, source_id: str) -> list[RunRecord]:
        """Recent run history for ``source_id``, newest first."""
        return list(self._runs.get(source_id, []))

    def last_run(self, source_id: str) -> RunRecord | None:
        """The most recent run outcome for ``source_id``, if any."""
        history = self._runs.get(source_id)
        return history[0] if history else None

    def sources(self) -> list[tuple[str, dict[str, object]]]:
        """Every known source entry as ``(source_id, entry)``."""
        return sorted(self._sources.items())

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

    def _load_runs(self) -> dict[str, list[RunRecord]]:
        """Decode the run ledger, tolerating its absence in older state files."""
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        runs = data.get("runs")
        if not isinstance(runs, dict):
            return {}
        loaded: dict[str, list[RunRecord]] = {}
        for source_id, history in runs.items():
            if not isinstance(history, list):
                continue
            records = [RunRecord.from_dict(r) for r in history if isinstance(r, dict)]
            if records:
                loaded[str(source_id)] = records
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


__all__ = ["DocEntry", "DocStore", "RunRecord"]