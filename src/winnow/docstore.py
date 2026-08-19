"""Per-source document state for incremental ingestion.

Idempotent re-runs already avoid duplicating points; incremental ingestion
adds *skipping*: documents whose content fingerprint is unchanged are not
re-extracted, re-chunked, or re-indexed.

State is keyed by source identity **and** by a pipeline signature (extract /
chunk / embed settings): changing the pipeline invalidates stored
fingerprints instead of silently serving stale chunks. State is committed
only after a successful run, so a failed run leaves the previous state
intact.

Phase 1 stores the full content hash, so unchanged documents still get
fetched (listed + bodies downloaded) but the heavy stages are skipped.
Phase 2 will let connectors surface a cheap listing fingerprint (S3 ETag,
GitLab/GitHub blob sha, Confluence version) so downloads can be skipped too.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

_VERSION = 1


class DocStore:
    """A JSON-backed map of ``source identity -> {uri -> fingerprint}``.

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

    def fingerprint(self, source_id: str, signature: str, uri: str) -> str | None:
        """Stored fingerprint of ``uri``, or ``None`` if this source is new
        or was last ingested with a different pipeline signature."""
        entry = self._sources.get(source_id)
        if entry is None or entry.get("signature") != signature:
            return None
        documents = entry.get("documents")
        if not isinstance(documents, dict):
            return None
        value = documents.get(uri)
        return value if isinstance(value, str) else None

    def commit(
        self,
        source_id: str,
        signature: str,
        current: Mapping[str, str],
    ) -> int:
        """Replace this source's state with the latest run's fingerprints.

        ``current`` maps ``uri -> fingerprint`` for the documents present in
        this run. Returns how many previously known documents disappeared.
        """
        previous = self._sources.get(source_id, {}).get("documents", {})
        if not isinstance(previous, dict):
            previous = {}
        deleted = sum(1 for uri in previous if uri not in current)
        self._sources[source_id] = {
            "signature": signature,
            "documents": dict(current),
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
        return {key: (value if isinstance(value, dict) else {}) for key, value in sources.items()}


__all__ = ["DocStore"]