"""Plugin SDK — the external extension contract (roadmap v1.1).

Winnow's stage adapters — ``source``, ``extractor``, ``chunker``,
``embedder``, ``indexer`` — are pluggable. A plugin is an ordinary installed
Python package that registers one or more adapters through a
:class:`Plugin` declaration, its own ``pyproject.toml`` entry point, and a
per-kind factory. No core change is required to ship a new adapter: discovery,
validation (``winnow validate``/``--dry-run``), and the stage factories all
consult the plugin registry.

A minimal plugin package::

    # kb_plugin/__init__.py
    from winnow.plugin import Plugin
    from winnow.sources.base import Source

    def _catalog(*, config, max_bytes=None) -> Source: ...

    plugin = Plugin(
        name="kb-catalog",
        version="0.1.0",
        summary="Reads a corporate catalog directory",
        sources={"catalog": _catalog},
    )

    # pyproject.toml
    [project.entry-points."winnow.plugins"]
    kb-catalog = "kb_plugin:plugin"

The entry point value may be the ``Plugin`` instance itself (as above) or a
callable returning one.

After ``pip install kb-catalog`` the adapter just works::

    pipeline:
      source:
        type: catalog
        config:
          path: ./data

Factory contract (all keyword-only):

- source:    ``factory(*, config: SourceConfig, max_bytes: int | None = None)``
- extractor: ``factory(*, config: ExtractConfig)``
- chunker:   ``factory(*, config: ChunkConfig)``
- embedder:  ``factory(*, config: EmbedConfig)``
- indexer:   ``factory(*, config: IndexConfig)``

The factory receives the stage's already-validated config model, so plugin
validation stays in the adapter (mirroring how built-ins validate in their
module) and the YAML schema stays open to new keys.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint

KINDS = ("source", "extractor", "chunker", "embedder", "indexer")
"""The five pluggable adapter kinds, used as registry keys."""

ENTRY_POINT_GROUP = "winnow.plugins"
"""Entry-point group under which plugin packages register themselves."""

Factory = Callable[..., object]
EntryPointsLoader = Callable[[str], list[EntryPoint]]

_KIND_ATTRS: dict[str, str] = {
    "source": "sources",
    "extractor": "extractors",
    "chunker": "chunkers",
    "embedder": "embedders",
    "indexer": "indexers",
}


@dataclass
class Plugin:
    """One installable extension package.

    Each named adapter kind maps *type strings* (the value of ``type:`` /
    ``strategy:`` in a pipeline config) to a factory implementing the
    contract documented in :mod:`winnow.plugin`.
    """

    name: str
    version: str
    summary: str = ""
    sources: Mapping[str, Factory] = field(default_factory=dict)
    extractors: Mapping[str, Factory] = field(default_factory=dict)
    chunkers: Mapping[str, Factory] = field(default_factory=dict)
    embedders: Mapping[str, Factory] = field(default_factory=dict)
    indexers: Mapping[str, Factory] = field(default_factory=dict)

    def provided_types(self, kind: str) -> dict[str, Factory]:
        """The type-string → factory map for ``kind`` (empty if not provided)."""
        return dict(getattr(self, _KIND_ATTRS[kind]))


class PluginRegistry:
    """Holds installed plugins and answers lookups for their type strings."""

    def __init__(self) -> None:
        self._plugins: list[Plugin] = []
        self._loaded_entry_points: set[tuple[str, str]] = set()
        self._by_kind: dict[str, dict[str, Factory]] = {kind: {} for kind in KINDS}

    def install(self, plugin: Plugin) -> None:
        for kind in KINDS:
            for type_str, factory in plugin.provided_types(kind).items():
                existing = self._by_kind[kind].get(type_str)
                if existing is not None and existing is not factory:
                    raise ValueError(
                        f"plugin {plugin.name!r} registers {kind} {type_str!r}, "
                        "which is already provided by another plugin"
                    )
                self._by_kind[kind][type_str] = factory
        self._plugins.append(plugin)

    def discover(
        self,
        *,
        entry_points: EntryPointsLoader | None = None,
    ) -> list[Plugin]:
        """Load plugins from installed packages' ``winnow.plugins`` entry points.

        Each entry point must resolve to either a :class:`Plugin` instance or
        a callable returning one. A broken entry point is skipped with a
        warning so one bad package cannot take the whole CLI down.
        """
        loader = entry_points if entry_points is not None else _iter_entry_points
        discovered: list[Plugin] = []
        for entry_point in loader(ENTRY_POINT_GROUP):
            key = (entry_point.name, entry_point.value)
            if key in self._loaded_entry_points:
                continue
            self._loaded_entry_points.add(key)
            try:
                loaded = entry_point.load()
                plugin = loaded() if callable(loaded) else loaded
            except Exception as exc:  # any failure in a third-party package
                warnings.warn(
                    f"plugin entry point {entry_point!r} failed to load: {exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )
                continue
            if not isinstance(plugin, Plugin):
                warnings.warn(
                    f"plugin entry point {entry_point!r} did not return a winnow.plugin.Plugin",
                    RuntimeWarning,
                    stacklevel=2,
                )
                continue
            self.install(plugin)
            discovered.append(plugin)
        return discovered

    def plugins(self) -> list[Plugin]:
        return list(self._plugins)

    def factory(self, kind: str, type_str: str) -> Factory | None:
        return self._by_kind[kind].get(type_str)

    def known_types(self, kind: str) -> set[str]:
        return set(self._by_kind[kind])

    def __iter__(self) -> Iterator[Plugin]:
        return iter(self._plugins)


_registry: PluginRegistry | None = None


def _iter_entry_points(group: str) -> list[EntryPoint]:
    from importlib.metadata import entry_points

    return list(entry_points(group=group))


def registry() -> PluginRegistry:
    """The process-wide plugin registry (created lazily)."""
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry


def install_plugin(plugin: Plugin) -> None:
    """Register a plugin programmatically (skip entry-point discovery)."""
    registry().install(plugin)


def discover_plugins() -> list[Plugin]:
    """Discover and install every plugin available on the import path."""
    return registry().discover()


def reset_registry() -> None:
    """Drop all installed plugins (test helper)."""
    global _registry
    _registry = PluginRegistry()


# Re-exported so plugin authors only need one import.
__all__ = [
    "ENTRY_POINT_GROUP",
    "KINDS",
    "Plugin",
    "PluginRegistry",
    "discover_plugins",
    "install_plugin",
    "registry",
    "reset_registry",
]