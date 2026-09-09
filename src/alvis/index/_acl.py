"""Shared ACL visibility check for the Python-side (memory/sqlite) indexes.

A point is visible to ``principals`` if it carries no ``acl`` metadata
(public) or its ``acl`` intersects ``principals``. ``principals=None`` means
no identity was supplied — no filtering, everything is visible (existing
callers that never pass an identity see unchanged behaviour).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def acl_visible(metadata: Mapping[str, object], principals: Sequence[str] | None) -> bool:
    if principals is None:
        return True
    acl = metadata.get("acl")
    if not isinstance(acl, (list, tuple)) or not acl:
        return True
    return bool({str(p) for p in acl} & {str(p) for p in principals})
