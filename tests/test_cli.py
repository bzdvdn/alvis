from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from alvis.cli import app
from alvis.plugin import reset_registry

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_registry() -> None:
    reset_registry()
    yield
    reset_registry()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == "alvis 1.0.0rc3"


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


def test_validate_ok_elasticsearch_index(tmp_path: Path) -> None:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: confluence\n"
        "  index:\n"
        "    type: elasticsearch\n"
        "    config:\n"
        "      url: http://localhost:9200\n"
        "      index: alvis_docs\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(config)])
    assert result.exit_code == 0
    assert "is valid" in result.output


def test_validate_ok_notion_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTION_API_TOKEN", "secret")
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: notion\n"
        "    config:\n"
        "      api_token_env: NOTION_API_TOKEN\n"
        "  index:\n"
        "    type: qdrant\n"
        "    config:\n"
        "      url: http://localhost:6333\n"
        "      collection: docs\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(config)])
    assert result.exit_code == 0
    assert "is valid" in result.output


def test_validate_ok_jira_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JIRA_API_TOKEN", "secret")
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: jira\n"
        "    config:\n"
        "      url: https://acme.atlassian.net\n"
        "      project: ENG\n"
        "      username: you@example.com\n"
        "      api_token_env: JIRA_API_TOKEN\n"
        "  index:\n"
        "    type: qdrant\n"
        "    config:\n"
        "      url: http://localhost:6333\n"
        "      collection: docs\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(config)])
    assert result.exit_code == 0
    assert "is valid" in result.output


def test_validate_ok_sharepoint_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SP_CLIENT_SECRET", "secret")
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: sharepoint\n"
        "    config:\n"
        "      tenant_id: tenant-1\n"
        "      client_id: client-1\n"
        "      site_url: https://contoso.sharepoint.com/sites/TeamSite\n"
        "      client_secret_env: SP_CLIENT_SECRET\n"
        "  index:\n"
        "    type: qdrant\n"
        "    config:\n"
        "      url: http://localhost:6333\n"
        "      collection: docs\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(config)])
    assert result.exit_code == 0
    assert "is valid" in result.output


def test_validate_ok_gdrive_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GDRIVE_KEY", '{"client_email": "x", "private_key": "y"}')
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: gdrive\n"
        "    config:\n"
        "      service_account_key_env: GDRIVE_KEY\n"
        "  index:\n"
        "    type: qdrant\n"
        "    config:\n"
        "      url: http://localhost:6333\n"
        "      collection: docs\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(config)])
    assert result.exit_code == 0
    assert "is valid" in result.output


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
    assert (tmp_path / "alvis.yaml").exists()


def test_init_refuses_overwrite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "alvis.yaml"
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
        "      collection: alvis_docs\n",
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
    pipelines = tmp_path / "alvis" / "pipelines"
    pipelines.mkdir(parents=True)
    (pipelines / "one.yaml").write_text(_fs_pipeline(corpus), encoding="utf-8")
    (pipelines / "two.yaml").write_text(_fs_pipeline(corpus), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run"])
    assert result.exit_code == 0
    assert "alvis/pipelines/one.yaml: 1 documents" in result.output
    assert "alvis/pipelines/two.yaml: 1 documents" in result.output


def test_run_without_configs_prefers_alvis_yaml(tmp_path: Path, monkeypatch) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    (tmp_path / "alvis.yaml").write_text(_fs_pipeline(corpus), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run"])
    assert result.exit_code == 0
    assert "alvis.yaml: 1 documents" in result.output


def test_run_without_configs_errors_when_nothing_found(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 2
    assert "alvis init" in result.output


def test_validate_accepts_plugin_type_from_local_dir(tmp_path: Path, monkeypatch) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "svc_demo.py").write_text(
        "from alvis.plugin import Plugin\n\n"
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


def test_init_writes_selected_source_and_index(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--source", "fs", "--index", "pgvector"])
    assert result.exit_code == 0
    target = tmp_path / "alvis.yaml"
    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert "type: fs" in text
    assert "type: pgvector" in text
    assert "dsn_env" in text
    assert "POSTGRES_DSN" in text


def test_init_rejects_unknown_source_or_index(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--source", "nope"])
    assert result.exit_code != 0
    assert "unknown source" in result.output


def test_validate_reports_missing_environment_variable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: confluence\n"
        "    config:\n"
        "      api_token_env: CONFLUENCE_API_TOKEN\n"
        "  index:\n"
        "    type: qdrant\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(config)])
    assert result.exit_code == 1
    assert "CONFLUENCE_API_TOKEN" in result.output


def test_validate_env_file_satisfies_missing_variable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('CONFLUENCE_API_TOKEN="secret"\n', encoding="utf-8")
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: confluence\n"
        "    config:\n"
        "      api_token_env: CONFLUENCE_API_TOKEN\n"
        "  index:\n"
        "    type: qdrant\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(config), "--env-file", str(env_file)])
    assert result.exit_code == 0
    assert "is valid" in result.output


def test_status_reports_last_run_from_ledger(tmp_path: Path, monkeypatch) -> None:
    import json

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\nhello content", encoding="utf-8")
    state = tmp_path / "state.json"
    config = tmp_path / "pipeline.yaml"
    config.write_text(_fs_pipeline(corpus), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    run_result = runner.invoke(
        app,
        ["run", str(config), "--incremental", "--state", str(state)],
    )
    assert run_result.exit_code == 0

    result = runner.invoke(app, ["status", str(config), "--state", str(state), "--json"])
    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report[0]["path"] == str(config)
    assert report[0]["source_health"]["ok"] is True
    assert report[0]["documents_known"] == 1
    last_run = report[0]["last_run"]
    assert last_run is not None
    assert last_run["ok"] is True
    assert last_run["documents"] == 1


def test_status_reports_invalid_config(tmp_path: Path) -> None:
    import json

    config = tmp_path / "pipeline.yaml"
    config.write_text("foo: bar\n", encoding="utf-8")
    result = runner.invoke(app, ["status", str(config), "--json"])
    assert result.exit_code == 0
    report = json.loads(result.output)
    assert "config_error" in report[0]


def test_metrics_serves_prometheus_and_404(tmp_path: Path) -> None:
    import urllib.request

    from alvis.cli import _start_metrics_server

    server = _start_metrics_server("127.0.0.1", 0)
    assert server is not None
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(  # noqa: S310
            f"http://127.0.0.1:{port}/metrics", timeout=5
        ) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/plain")
            body = response.read().decode()
        assert "counter" in body or body == ""

        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(  # noqa: S310
                f"http://127.0.0.1:{port}/other", timeout=5
            )
        assert excinfo.value.code == 404
    finally:
        server.shutdown()


def _confluence_pipeline_with_token(tmp_path: Path, token_env: str) -> Path:
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: confluence\n"
        "    config:\n"
        f"      api_token_env: {token_env}\n"
        "  index:\n"
        "    type: qdrant\n",
        encoding="utf-8",
    )
    return config


def test_validate_loads_conventional_dotenv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    (tmp_path / ".env").write_text('CONFLUENCE_API_TOKEN="secret"\n', encoding="utf-8")
    config = _confluence_pipeline_with_token(tmp_path, "CONFLUENCE_API_TOKEN")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["validate", str(config)])
    assert result.exit_code == 0
    assert "is valid" in result.output


def test_run_fails_fast_on_missing_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    config = _confluence_pipeline_with_token(tmp_path, "CONFLUENCE_API_TOKEN")
    result = runner.invoke(app, ["run", str(config)])
    assert result.exit_code == 1
    assert "Missing environment variable(s)" in result.output
    assert "CONFLUENCE_API_TOKEN" in result.output


def test_plugins_lists_local_plugins(tmp_path: Path, monkeypatch) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "svc_demo.py").write_text(
        "from alvis.plugin import Plugin\n\n"
        "def _svc(*, config, max_bytes=None):\n"
        "    raise NotImplementedError\n\n"
        'plugin = Plugin(name="svc", version="1.0.0", '
        'sources={"svc_demo": _svc})\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["plugins", "--plugins", str(plugins)])
    assert result.exit_code == 0
    assert "svc" in result.output
    assert "svc_demo" in result.output


def test_run_rejects_bad_options(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", "--log-level", "bogus"])
    assert result.exit_code == 2

    result = runner.invoke(app, ["run", "--parallel", "0"])
    assert result.exit_code == 2

    result = runner.invoke(app, ["run", "--interval", "0"])
    assert result.exit_code == 2

    result = runner.invoke(app, ["run", "--metrics-port", "-1"])
    assert result.exit_code == 2


def test_run_watch_implies_incremental(tmp_path: Path, monkeypatch) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\nhello", encoding="utf-8")
    config = tmp_path / "pipeline.yaml"
    config.write_text(_fs_pipeline(corpus), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def _interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    # `run --watch` calls the real `asyncio` module, wherever `alvis.cli._run`
    # imported it from — patching the module object directly (rather than an
    # attribute on `alvis.cli`) affects it regardless of which submodule holds
    # the `run` command's implementation.
    monkeypatch.setattr(asyncio, "sleep", _interrupt)
    result = runner.invoke(
        app, ["run", str(config), "--watch", "--interval", "0.001"]
    )
    assert result.exit_code == 0
    assert "--watch implies --incremental" in result.output
    assert "Watching stopped" in result.output


def test_status_text_render_shows_run(tmp_path: Path, monkeypatch) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\nhello content", encoding="utf-8")
    state = tmp_path / "state.json"
    config = tmp_path / "pipeline.yaml"
    config.write_text(_fs_pipeline(corpus), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    runner.invoke(app, ["run", str(config), "--incremental", "--state", str(state)])

    result = runner.invoke(app, ["status", str(config), "--state", str(state)])
    assert result.exit_code == 0
    assert "source health: OK" in result.output
    assert "last run:" in result.output
    assert "embed cache:" in result.output
    assert "index points:" in result.output


def test_status_probe_unreachable_index(tmp_path: Path, monkeypatch) -> None:
    import alvis.cli._status as status_cmd

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\nhello", encoding="utf-8")
    state = tmp_path / "state.json"
    config = tmp_path / "pipeline.yaml"
    config.write_text(_fs_pipeline(corpus), encoding="utf-8")

    class _Unreachable:
        async def count(self) -> int:
            raise ConnectionError("down")

        def __getattr__(self, name: str) -> object:
            raise ConnectionError("down")

    monkeypatch.setattr(status_cmd, "build_indexer", lambda _index: _Unreachable())
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app, ["status", str(config), "--state", str(state), "--probe"]
    )
    assert result.exit_code == 0
    assert "unreachable" in result.output


def test_chat_multi_turn_session_without_llm_key(
    tmp_path: Path, monkeypatch
) -> None:
    """No chat API key configured -> offline citation fallback each turn."""
    from alvis.core.models import Chunk
    from alvis.embed.hash import HashEmbedder
    from alvis.index import MemoryIndex

    config = tmp_path / "p.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {tmp_path}\n"
        "  index:\n"
        "    type: memory\n",
        encoding="utf-8",
    )
    indexer = MemoryIndex()
    embedder = HashEmbedder()
    vector = asyncio.run(embedder.embed(Chunk(text="install alvis", source_uri="q")))
    asyncio.run(
        indexer.upsert(
            Chunk(text="install alvis via pip", source_uri="u/install"),
            vector,
            source_id="s",
            artifact_hash="h",
        )
    )
    monkeypatch.setattr("alvis.pipeline.runner.build_indexer", lambda *a, **k: indexer)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = runner.invoke(
        app,
        ["chat", str(config)],
        input="how do I install it?\nexit\n",
    )

    assert result.exit_code == 0
    assert "Alvis chat" in result.output
    assert "u/install" in result.output
    assert "not set; falling back to numbered excerpts" in result.output


def test_chat_rejects_malformed_filter(tmp_path: Path) -> None:
    config = tmp_path / "p.yaml"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {tmp_path}\n"
        "  index:\n"
        "    type: memory\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["chat", str(config), "--filter", "no-equals-sign"], input="exit\n"
    )
    assert result.exit_code == 1
    assert "Error" in result.output