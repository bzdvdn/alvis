from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from winnow.cli import app

runner = CliRunner()


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