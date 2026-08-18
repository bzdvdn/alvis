"""Deterministic identifiers for index points (idempotency)."""

from __future__ import annotations

import uuid


def point_id(source_uri: str, chunk_text: str) -> uuid.UUID:
    """Deterministic point ID so re-upserting identical content replaces,
    not duplicates."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{source_uri}\n{chunk_text}")