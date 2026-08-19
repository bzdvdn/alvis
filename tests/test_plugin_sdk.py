"""Plugin SDK (v1.1) — registering, discovering, and running plugins.

The example package in ``examples/kb-plugin`` is the reference: it must run
through the SDK end-to-end (install → accepted → built → ingested) without any
core change, which is exactly the roadmap's "done" criteria for a side project.
"""

from __future__ import annotations

import inspect
import sys
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from winnow import dsl, run_async
from winnow.config.models import SourceConfig
from winnow.factories import build_source
from winnow.index import MemoryIndex
from winnow.plugin import (
    KINDS,
    Plugin,
    PluginRegistry,
    install_plugin,
    load_local_plugins,
    registry,
    reset_registry,
)
from winnow.registry import check_pipeline_supported

_EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "kb-plugin"


@pytest.fixture(autouse=True)
def _isolated_registry() -> None:
    reset_registry()
    yield
    reset_registry()


def _install_example() -> Plugin:
    sys.path.insert(0, str(_EXAMPLE))
    from kb_plugin import plugin

    return plugin


async def test_plugin_source_is_accepted_after_install() -> None:
    install_plugin(_install_example())
    assert check_pipeline_supported(
        source="catalog", extract="auto", chunk="auto", embed="default", index="memory"
    ) == []


async def test_build_source_constructs_plugin_source() -> None:
    install_plugin(_install_example())
    config = SourceConfig(type="catalog", config={"path": str(_EXAMPLE / "data")})
    source = build_source(config)
    metas = await source.list_documents()
    assert len(metas) == 2
    assert {meta.step_id for meta in metas} == {
        str(_EXAMPLE / "data" / "incremental-ingestion.txt"),
        str(_EXAMPLE / "data" / "scalable-vector-search.txt"),
    }


async def test_plugin_source_runs_pipeline(tmp_path: Path) -> None:
    install_plugin(_install_example())
    cfg = dsl.pipeline(
        SourceConfig(
            type="catalog",
            config={"path": str(_EXAMPLE / "data"), "tag": "products"},
        ),
        index=dsl.memory(),
    )
    indexer = MemoryIndex()
    state = tmp_path / "state.json"
    first = await run_async(cfg, indexer=indexer, incremental=True, state_path=str(state))
    assert first.documents_ingested == 2
    assert first.documents_changed == 2
    assert first.chunks_indexed == 2

    hits = await indexer.search([0.0] * 768, top_k=4)
    assert len(hits) == 2
    assert all(hit.metadata.get("catalog") == "products" for hit in hits)

    second = await run_async(cfg, indexer=indexer, incremental=True, state_path=str(state))
    assert second.documents_skipped == 2
    assert second.documents_changed == 0


def test_discovery_loads_plugins_from_entry_points() -> None:
    sys.path.insert(0, str(_EXAMPLE))
    ep = EntryPoint(name="kb-catalog", value="kb_plugin:plugin", group="winnow.plugins")

    reg = PluginRegistry()
    assert reg.known_types("source") == set()
    discovered = reg.discover(entry_points=lambda group: [ep])
    assert [p.name for p in discovered] == ["kb-catalog"]
    assert reg.known_types("source") == {"catalog"}

    assert reg.discover(entry_points=lambda group: [ep]) == []


def test_discovery_skips_broken_entry_points(recwarn: pytest.WarningsRecorder) -> None:
    broken = EntryPoint(name="broken", value="no_such_module_xyz:plugin", group="winnow.plugins")
    reg = PluginRegistry()
    assert reg.discover(entry_points=lambda group: [broken]) == []
    assert len(recwarn) == 1


def test_duplicate_type_registration_is_rejected() -> None:
    install_plugin(_install_example())
    imposter = Plugin(name="imposter", version="0.0.0", sources={"catalog": lambda **kw: None})
    with pytest.raises(ValueError, match="already provided"):
        install_plugin(imposter)


def test_discovery_uses_the_process_registry_reset_clean() -> None:
    assert PluginRegistry().known_types("source") == set()


def _write_local_plugin(directory: Path, name: str, source_type: str) -> Path:
    module = directory / f"{name}.py"
    module.write_text(
        "from winnow.plugin import Plugin\n"
        "\n"
        f"def _svc(*, config, max_bytes=None):\n"
        "    raise NotImplementedError\n"
        "\n"
        f'plugin = Plugin(name="{name}", version="1.0.0", '
        f'sources={{"{source_type}": _svc}})\n',
        encoding="utf-8",
    )
    return module


def test_load_local_plugins_registers_types(tmp_path: Path) -> None:
    _write_local_plugin(tmp_path, "svc_demo", source_type="svc_demo")
    loaded = load_local_plugins([tmp_path])
    assert [p.name for p in loaded] == ["svc_demo"]
    assert len(load_local_plugins([tmp_path])) == 1  # re-run does not re-import


def test_load_local_plugins_skips_broken_and_underscore_files(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    _write_local_plugin(tmp_path, "svc_ok", source_type="svc_ok")
    (tmp_path / "_helpers.py").write_text(
        "raise RuntimeError('never imported')\n", encoding="utf-8"
    )
    (tmp_path / "broken.py").write_text("raise ValueError('boom')\n", encoding="utf-8")
    loaded = load_local_plugins([tmp_path])
    assert [p.name for p in loaded] == ["svc_ok"]
    assert len(recwarn) == 1
    assert "broken.py" in str(recwarn.pop().message)


def test_load_local_plugins_accepts_install_plugin_convention(tmp_path: Path) -> None:
    (tmp_path / "explicit.py").write_text(
        "from winnow.plugin import Plugin, install_plugin, registry\n"
        "\n"
        "def _svc(*, config, max_bytes=None):\n"
        "    raise NotImplementedError\n"
        "\n"
        'install_plugin(Plugin(name="explicit", version="1.0.0", '
        'sources={"explicit_svc": _svc}))\n',
        encoding="utf-8",
    )
    load_local_plugins([tmp_path])
    assert "explicit_svc" in registry().known_types("source")


_FACTORY_CONTRACT = {
    "source": frozenset({"config", "max_bytes"}),
    "extractor": frozenset({"config"}),
    "chunker": frozenset({"config"}),
    "embedder": frozenset({"config"}),
    "indexer": frozenset({"config"}),
}


def assert_factory_contract(kind: str, type_str: str, factory) -> None:
    """The documented factory contract: keyword-only, required ``config``."""
    params = inspect.signature(factory).parameters
    name = f"{kind}::{type_str}"
    bad_kind = [p for p in params.values() if p.kind != inspect.Parameter.KEYWORD_ONLY]
    assert not bad_kind, f"{name} has non-keyword-only params: {[p.name for p in bad_kind]}"
    expected = _FACTORY_CONTRACT[kind]
    assert params.keys() <= expected, (
        f"{name} exposes unexpected params {sorted(params.keys() - expected)}"
    )
    assert "config" in params, f"{name} is missing required param 'config'"
    if "max_bytes" in params:
        assert params["max_bytes"].default is None, f"{name} max_bytes default must be None"


def _mk(kind: str):
    if kind == "source":

        def _factory(*, config, max_bytes=None):
            return kind

    else:

        def _factory(*, config):
            return kind

    return _factory


@pytest.mark.parametrize("kind", ["source", "extractor", "chunker", "embedder", "indexer"])
def test_plugin_factory_contract(kind: str) -> None:
    reg = PluginRegistry()
    reg.install(_install_example())  # reference plugin: source with compliant signature
    for candidate_kind in KINDS:
        reg.install(
            Plugin(
                name=f"contract-{candidate_kind}",
                version="1.0.0",
                **{
                    {
                        "source": "sources",
                        "extractor": "extractors",
                        "chunker": "chunkers",
                        "embedder": "embedders",
                        "indexer": "indexers",
                    }[candidate_kind]: {
                        f"ct_{candidate_kind}": _mk(candidate_kind)
                    }
                },
            )
        )
    factories = [
        (type_str, factory)
        for plugin in reg.plugins()
        for type_str, factory in plugin.provided_types(kind).items()
    ]
    assert factories, f"expected a {kind} factory to register"
    for type_str, factory in factories:
        assert_factory_contract(kind, type_str, factory)


@pytest.mark.parametrize(
    "kind, factory",
    [
        ("source", lambda config, *, max_bytes=None: object()),  # positional config
        ("chunker", lambda *, cfg: object()),  # wrong param name
        ("embedder", lambda **kwargs: object()),  # var-keyword only, no config
    ],
)
def test_plugin_factory_contract_rejects_violations(kind: str, factory) -> None:
    with pytest.raises(AssertionError):
        assert_factory_contract(kind, "bad", factory)