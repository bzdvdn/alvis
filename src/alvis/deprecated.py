"""Deprecation policy — loud removal markers over a package-level window.

The package follows the semver + deprecation policy in ``docs/versioning.md``:
public API may be marked deprecated in one release and removed only in a later
one. Deprecations are **never** silent — :func:`deprecated` emits a
``DeprecationWarning`` (once per call site) carrying the removal version, so
downstream users get an explicit migration cue before a break.

Use it on any public name that must change shape:

    from alvis.deprecated import deprecated

    @deprecated("use alvis.load_pipeline instead", remove_in="1.0.0")
    def load_config(path: str) -> PipelineConfig: ...
"""

from __future__ import annotations

import functools
import warnings
from collections.abc import Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


def deprecated(
    reason: str,
    *,
    remove_in: str,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorate a callable as deprecated, scheduled for removal in ``remove_in``.

    ``remove_in`` is the package version that will drop the name; the warning
    names it so callers know the migration deadline. Emitted once per call
    site via :func:`warnings.warn` with ``stacklevel=2``.
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        qualified = getattr(func, "__qualname__", func.__name__)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            warnings.warn(
                f"{qualified} is deprecated: {reason} (removed in {remove_in})",
                DeprecationWarning,
                stacklevel=2,
            )
            return func(*args, **kwargs)

        wrapper.__deprecated__ = {  # type: ignore[attr-defined]
            "reason": reason,
            "remove_in": remove_in,
        }
        return wrapper

    return decorator


def is_deprecated(callable_: object) -> bool:
    """Whether ``callable_`` is marked by :func:`deprecated` (test/audit helper)."""
    return bool(getattr(callable_, "__deprecated__", False))


def removed_in(callable_: object) -> str | None:
    """The removal version carried by a deprecated callable, if any."""
    marker = getattr(callable_, "__deprecated__", None)
    if not isinstance(marker, dict):
        return None
    removal = marker.get("remove_in")
    return removal if isinstance(removal, str) else None