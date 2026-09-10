"""Retrieval-quality evaluation harness (:mod:`alvis.evaluation`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from alvis import dsl, run_async
from alvis.config import ConfigError, PipelineConfig
from alvis.evaluation import EvalCase, evaluate_async, load_cases
from alvis.index import MemoryIndex

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


async def _seeded_docs_index() -> MemoryIndex:
    config = dsl.pipeline(
        dsl.fs(str(EXAMPLES / "docs")),
        chunk=dsl.chunk(strategy="sections", max_tokens=200),
        index=dsl.memory(),
    )
    indexer = MemoryIndex()
    await run_async(config, indexer=indexer)
    return indexer


def _config() -> PipelineConfig:
    return dsl.pipeline(
        dsl.fs(str(EXAMPLES / "docs")),
        chunk=dsl.chunk(strategy="sections", max_tokens=200),
        index=dsl.memory(),
    )


def test_load_cases_from_example_fixture() -> None:
    cases = load_cases(EXAMPLES / "eval-cases.yaml")
    assert len(cases) == 4
    assert all(isinstance(c, EvalCase) for c in cases)
    assert cases[0].query == "how do I install alvis with pip"


def test_load_cases_rejects_empty_or_malformed(tmp_path: Path) -> None:
    empty = tmp_path / "empty.yaml"
    empty.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="non-empty"):
        load_cases(empty)

    malformed = tmp_path / "bad.yaml"
    malformed.write_text("- query: only a query, no expected_source_uri\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid eval case"):
        load_cases(malformed)


async def test_evaluate_example_fixture_hits_every_case() -> None:
    """The example fixture (examples/docs + eval-cases.yaml) is the golden
    retrieval-quality baseline: it must stay at 100% hit rate as chunking or
    embedding defaults change, or the harness itself has regressed."""
    indexer = await _seeded_docs_index()
    cases = load_cases(EXAMPLES / "eval-cases.yaml")

    report = await evaluate_async(_config(), cases, top_k=3, indexer=indexer)

    assert report.hit_rate == pytest.approx(1.0)
    assert report.mrr == pytest.approx(1.0)
    assert not report.misses
    for result in report.results:
        assert result.rank == 1


async def test_evaluate_reports_misses_for_unrelated_query() -> None:
    indexer = await _seeded_docs_index()
    cases = [EvalCase(query="how do I install alvis", expected_source_uri="nonexistent.md")]

    report = await evaluate_async(_config(), cases, top_k=3, indexer=indexer)

    assert report.hit_rate == 0.0
    assert report.mrr == 0.0
    assert len(report.misses) == 1
    assert report.misses[0].rank is None


async def test_evaluate_async_judges_answer_quality_with_llm_and_judge() -> None:
    import httpx

    from alvis.answer import Synthesizer
    from alvis.judge import AnswerJudge

    indexer = await _seeded_docs_index()
    cases = load_cases(EXAMPLES / "eval-cases.yaml")[:1]

    async def answer_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Run pip install alvis [1]."}}]}
        )

    async def judge_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"faithfulness": 1.0, "relevancy": 1.0, "reason": "matches"}'
                            )
                        }
                    }
                ]
            },
        )

    llm = Synthesizer(
        base_url="http://llm.local", model="m", transport=httpx.MockTransport(answer_handler)
    )
    judge = AnswerJudge(
        base_url="http://judge.local", model="m", transport=httpx.MockTransport(judge_handler)
    )

    report = await evaluate_async(
        _config(), cases, top_k=3, indexer=indexer, llm=llm, judge=judge
    )

    assert report.judged
    assert report.mean_faithfulness == pytest.approx(1.0)
    assert report.mean_relevancy == pytest.approx(1.0)
    assert report.results[0].answer is not None
    assert report.results[0].answer.text.startswith("Run pip install")
    assert report.results[0].judge is not None


async def test_evaluate_async_skips_judging_without_both_llm_and_judge() -> None:
    from alvis.answer import Synthesizer

    indexer = await _seeded_docs_index()
    cases = load_cases(EXAMPLES / "eval-cases.yaml")[:1]
    llm = Synthesizer(base_url="http://llm.local", model="m")

    report = await evaluate_async(_config(), cases, top_k=3, indexer=indexer, llm=llm)

    assert not report.judged
    assert report.results[0].answer is None
    assert report.mean_faithfulness == 0.0


def test_cli_eval_judge_warns_and_skips_without_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from alvis.cli import app

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = tmp_path / "p.yaml"
    docs = EXAMPLES / "docs"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {docs}\n"
        "  chunk:\n"
        "    strategy: sections\n"
        "  index:\n"
        "    type: sqlite\n"
        "    config:\n"
        f"      path: {tmp_path / 'eval-judge.db'}\n",
        encoding="utf-8",
    )
    cases = tmp_path / "cases.yaml"
    cases.write_text(
        "- query: how do I install alvis with pip\n"
        "  expected_source_uri: alvis-guide.md\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    assert runner.invoke(app, ["run", str(config)]).exit_code == 0

    result = runner.invoke(app, ["eval", str(config), str(cases), "--judge"])
    assert result.exit_code == 0
    assert "skipping answer-quality judging" in result.output
    assert "hit_rate=1.00" in result.output
    assert "answer quality" not in result.output


def test_cli_eval_reports_hit_rate(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from alvis.cli import app

    config = tmp_path / "p.yaml"
    docs = EXAMPLES / "docs"
    config.write_text(
        "pipeline:\n"
        "  source:\n"
        "    type: fs\n"
        "    config:\n"
        f"      path: {docs}\n"
        "  chunk:\n"
        "    strategy: sections\n"
        "  index:\n"
        "    type: sqlite\n"
        "    config:\n"
        f"      path: {tmp_path / 'eval.db'}\n",
        encoding="utf-8",
    )
    cases = tmp_path / "cases.yaml"
    cases.write_text(
        "- query: how do I install alvis with pip\n"
        "  expected_source_uri: alvis-guide.md\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    run_result = runner.invoke(app, ["run", str(config)])
    assert run_result.exit_code == 0, run_result.output

    eval_result = runner.invoke(app, ["eval", str(config), str(cases)])
    assert eval_result.exit_code == 0, eval_result.output
    assert "hit_rate=1.00" in eval_result.output

    gated = runner.invoke(
        app, ["eval", str(config), str(cases), "--min-hit-rate", "1.1"]
    )
    assert gated.exit_code == 1
