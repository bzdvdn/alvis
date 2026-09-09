"""Internal helper: best-effort async cleanup for adapters owning a client.

Not public API. Sources, indexers, and embedders that hold an HTTP
connection pool or DB connection optionally expose an async ``aclose()``;
this calls it if present and swallows (logging) any failure — cleanup must
never mask a run's real outcome, success or failure.
"""

from __future__ import annotations

import alvis.observability as ob


async def aclose_quietly(obj: object) -> None:
    """Await ``obj.aclose()`` if it exists and is callable; never raises."""
    closer = getattr(obj, "aclose", None)
    if not callable(closer):
        return
    try:
        await closer()
    except Exception:
        ob.LOG.warning("aclose failed for %s", type(obj).__name__, exc_info=True)
