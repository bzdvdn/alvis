from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from winnow.cli import app
from winnow.plugin import reset_registry

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_registry() -> None:
    reset_registry()
    yield
    reset_registry()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == "winnow 0.1.0"


def test_validate_ok(tmp_path: Path) -> None:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n  source:\n    type: confluence\n  index:\n    type: qdrant\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(config)])
    assert result.exit_code == 0
    assert "is valid" in result.output
    assert "source: confluence" in result.output


def test_validate_invalid_structure(tmp_path: Path) -> None:
    config = tmp_path / "pipeline.yaml"
    config.write_text("foo: bar\n", encoding="utf-8")
    result = runner.invoke(app, ["validate", str(config)])
    assert result.exit_code == 1
    assert "pipeline" in result.output


def test_validate_unsupported_source(tmp_path: Path) -> None:
    config = tmp_path / "pipeline.yaml"
    config.write_text("pipeline:\n  source:\n    type: service_now\n", encoding="utf-8")
    result = runner.invoke(app, ["validate", str(config)])
    assert result.exit_code == 1
    assert "service_now" in result.output


def test_init_creates_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / "winnow.yaml").exists()


def test_init_refuses_overwrite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "winnow.yaml"
    target.write_text("keep me\n", encoding="utf-8")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert target.read_text(encoding="utf-8") == "keep me\n"


def test_run_missing_config(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", str(tmp_path / "missing.yaml")])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_run_dry_run_describes_pipeline(tmp_path: Path) -> None:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n  source:\n    type: fs\n    config:\n      path: /tmp\n"
        "  index:\n    type: memory\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["run", str(config), "--dry-run"])
    assert result.exit_code == 0
    assert "dry-run" in result.output
    assert "source: fs" in result.output
    assert "index: memory" in result.output


def test_run_dry_run_rejects_unsupported(tmp_path: Path) -> None:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n  source:\n    type: service_now\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["run", str(config), "--dry-run"])
    assert result.exit_code == 1
    assert "service_now" in result.output


def test_validate_report_shows_stages_and_graph(tmp_path: Path) -> None:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {tmp_path}\n"
        "  chunk:\n"
        "    strategy: size\n"
        "    config:\n"
        "      max_chars: 100\n"
        "      overlap_chars: 10\n"
        "  embed:\n"
        "    type: openai\n"
        "    config:\n"
        "      model: text-embedding-3-small\n"
        "  index:\n"
        "    type: qdrant\n"
        "    config:\n"
        "      collection: winnow_docs\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(config)])
    assert result.exit_code == 0
    assert "is valid" in result.output
    assert "source: fs" in result.output
    assert "chunk: size (max_chars=100, overlap_chars=10)" in result.output
    assert "embed: openai:text-embedding-3-small" in result.output
    assert "index: qdrant" in result.output
    assert "Pipeline graph" in result.output
    assert "▼" in result.output


def test_validate_json_report(tmp_path: Path) -> None:
    import json

    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n  source:\n    type: confluence\n  index:\n    type: qdrant\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(config), "--json"])
    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["valid"] is True
    assert report["contract"] == {"schema_version": 1, "cct": 1}
    assert report["stages"]["source"] == "confluence"
    assert "▼" in report["graph"]


def test_validate_json_invalid_report(tmp_path: Path) -> None:
    import json

    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n  source:\n    type: service_now\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["validate", str(config), "--json"])
    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["valid"] is False
    assert any("service_now" in p for p in report["problems"])


def test_run_dry_run_prints_graph(tmp_path: Path) -> None:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n  source:\n    type: fs\n    config:\n      path: /tmp\n"
        "  index:\n    type: memory\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["run", str(config), "--dry-run"])
    assert result.exit_code == 0
    assert "Pipeline graph" in result.output
    assert "▼" in result.output


def _fs_pipeline(path: Path) -> str:
    return (
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        f"    config:\n      path: {path}\n"
        "  index:\n"
        "    type: memory\n"
    )


def test_run_without_configs_scans_workflow_dir(tmp_path: Path, monkeypatch) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    pipelines = tmp_path / "winnow" / "pipelines"
    pipelines.mkdir(parents=True)
    (pipelines / "one.yaml").write_text(_fs_pipeline(corpus), encoding="utf-8")
    (pipelines / "two.yaml").write_text(_fs_pipeline(corpus), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run"])
    assert result.exit_code == 0
    assert "winnow/pipelines/one.yaml: 1 documents" in result.output
    assert "winnow/pipelines/two.yaml: 1 documents" in result.output


def test_run_without_configs_prefers_winnow_yaml(tmp_path: Path, monkeypatch) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    (tmp_path / "winnow.yaml").write_text(_fs_pipeline(corpus), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run"])
    assert result.exit_code == 0
    assert "winnow.yaml: 1 documents" in result.output


def test_run_without_configs_errors_when_nothing_found(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 2
    assert "winnow init" in result.output


def test_validate_accepts_plugin_type_from_local_dir(tmp_path: Path, monkeypatch) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "svc_demo.py").write_text(
        "from winnow.plugin import Plugin\n\n"
        "def _svc(*, config, max_bytes=None):\n"
        "    raise NotImplementedError\n\n"
        'plugin = Plugin(name="svc", version="1.0.0", '
        'sources={"svc_demo": _svc})\n',
        encoding="utf-8",
    )
    config = tmp_path / "cfg.yaml"
    config.write_text("pipeline:\n  source:\n    type: svc_demo\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["validate", str(config), "--plugins", str(plugins)])
    assert result.exit_code == 0
    assert "source: svc_demo" in result.output


def test_validate_rejects_plugin_type_without_flag(tmp_path: Path) -> None:
    config = tmp_path / "pipeline.yaml"
    config.write_text("pipeline:\n  source:\n    type: svc_demo\n", encoding="utf-8")
    result = runner.invoke(app, ["validate", str(config)])
    assert result.exit_code == 1
    assert "svc_demo" in result.output