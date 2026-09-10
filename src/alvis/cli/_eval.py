"""``alvis eval`` — score retrieval quality against a fixed set of cases."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from alvis._lifecycle import aclose_quietly
from alvis.answer import Synthesizer
from alvis.cli._shared import _enable_plugins, _load_env_file, app
from alvis.config import ConfigError
from alvis.errors import PipelineError
from alvis.evaluation import EvalCase, EvalReport, evaluate_async, load_cases
from alvis.judge import AnswerJudge
from alvis.rerank import LLMReranker
from alvis.secrets import resolve_secret


async def _evaluate_and_close(
    config: str,
    cases: list[EvalCase],
    top_k: int,
    hybrid: bool,
    reranker: LLMReranker | None = None,
    principals: list[str] | None = None,
    llm: Synthesizer | None = None,
    judge: AnswerJudge | None = None,
) -> EvalReport:
    """``evaluate_async`` plus closing the CLI-owned LLM clients."""
    try:
        return await evaluate_async(
            config,
            cases,
            top_k=top_k,
            hybrid=hybrid,
            rerank=reranker,
            principals=principals,
            llm=llm,
            judge=judge,
        )
    finally:
        if reranker is not None:
            await aclose_quietly(reranker)
        if llm is not None:
            await aclose_quietly(llm)
        if judge is not None:
            await aclose_quietly(judge)


@app.command()
def eval(  # noqa: A001 - deliberate CLI verb, shadows builtin only as a local name
    config: str = typer.Argument(  # noqa: B008
        ...,
        help="Path to the pipeline YAML config (uses its embed + index stages).",
    ),
    cases: Path = typer.Argument(  # noqa: B008
        ...,
        help="YAML file: a list of {query, expected_source_uri} cases.",
    ),
    plugin_dirs: list[Path] = typer.Option(  # noqa: B008
        None,
        "--plugins",
        help="Directories of local companion-plugin .py files to load.",
    ),
    env_file: Path | None = typer.Option(  # noqa: B008
        None,
        "--env-file",
        help="Load secrets from this file (default: ./.env if present).",
    ),
    top_k: int = typer.Option(  # noqa: B008
        5,
        "--top-k",
        help="Number of nearest chunks to retrieve per case.",
    ),
    principal: list[str] = typer.Option(  # noqa: B008
        None,
        "--principal",
        help=(
            "Default caller identity (repeatable) for every case; a case's "
            "own 'principals' in the cases file overrides this."
        ),
    ),
    hybrid: bool = typer.Option(  # noqa: B008
        False,
        "--hybrid",
        help="Fuse dense search with BM25 keyword search (memory/sqlite indexes only).",
    ),
    rerank: bool = typer.Option(  # noqa: B008
        False,
        "--rerank",
        help="Reorder candidates via an LLM chat call before truncating to --top-k.",
    ),
    rerank_base_url: str = typer.Option(  # noqa: B008
        "https://api.openai.com/v1",
        "--rerank-base-url",
        help="OpenAI-compatible chat endpoint for --rerank.",
    ),
    rerank_model: str = typer.Option(  # noqa: B008
        "gpt-4o-mini",
        "--rerank-model",
        help="Chat model for --rerank.",
    ),
    rerank_api_token_env: str = typer.Option(  # noqa: B008
        "OPENAI_API_KEY",
        "--rerank-api-token-env",
        help="Env var holding the chat API token for --rerank.",
    ),
    judge: bool = typer.Option(  # noqa: B008
        False,
        "--judge",
        help="Also synthesize an answer per case and score it for faithfulness/"
        "relevancy via an LLM judge — a separate axis from retrieval hit rate.",
    ),
    answer_base_url: str = typer.Option(  # noqa: B008
        "https://api.openai.com/v1",
        "--answer-base-url",
        help="OpenAI-compatible chat endpoint used to synthesize each --judge answer.",
    ),
    answer_model: str = typer.Option(  # noqa: B008
        "gpt-4o-mini",
        "--answer-model",
        help="Chat model used to synthesize each --judge answer.",
    ),
    answer_api_token_env: str = typer.Option(  # noqa: B008
        "OPENAI_API_KEY",
        "--answer-api-token-env",
        help="Env var holding the chat API token for --judge's answer synthesis.",
    ),
    judge_base_url: str = typer.Option(  # noqa: B008
        "https://api.openai.com/v1",
        "--judge-base-url",
        help="OpenAI-compatible chat endpoint for the --judge model.",
    ),
    judge_model: str = typer.Option(  # noqa: B008
        "gpt-4o-mini",
        "--judge-model",
        help="Chat model for the --judge grader (can differ from --answer-model).",
    ),
    judge_api_token_env: str = typer.Option(  # noqa: B008
        "OPENAI_API_KEY",
        "--judge-api-token-env",
        help="Env var holding the chat API token for --judge's grading calls.",
    ),
    min_hit_rate: float = typer.Option(  # noqa: B008
        0.0,
        "--min-hit-rate",
        help="Exit 1 if hit rate falls below this threshold (CI gate).",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Machine-readable report for CI.",
    ),
) -> None:
    """Score retrieval quality: does each case's query surface its expected document?"""
    _enable_plugins(plugin_dirs)
    _load_env_file(env_file)
    if rerank and not resolve_secret(rerank_api_token_env):
        typer.echo(
            f"Warning: --rerank set but {rerank_api_token_env} is not set; "
            "skipping reranking.",
            err=True,
        )
        rerank = False
    if judge and not (
        resolve_secret(answer_api_token_env) and resolve_secret(judge_api_token_env)
    ):
        envs = (
            answer_api_token_env
            if answer_api_token_env == judge_api_token_env
            else f"{answer_api_token_env}/{judge_api_token_env}"
        )
        typer.echo(
            f"Warning: --judge set but {envs} is not set; "
            "skipping answer-quality judging.",
            err=True,
        )
        judge = False
    reranker = (
        LLMReranker(
            base_url=rerank_base_url, model=rerank_model, api_token_env=rerank_api_token_env
        )
        if rerank
        else None
    )
    llm = (
        Synthesizer(
            base_url=answer_base_url, model=answer_model, api_token_env=answer_api_token_env
        )
        if judge
        else None
    )
    answer_judge = (
        AnswerJudge(
            base_url=judge_base_url, model=judge_model, api_token_env=judge_api_token_env
        )
        if judge
        else None
    )
    try:
        eval_cases = load_cases(cases)
        report = asyncio.run(
            _evaluate_and_close(
                config, eval_cases, top_k, hybrid, reranker, principal, llm, answer_judge
            )
        )
    except (ConfigError, PipelineError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if json_output:
        report_json: dict[str, object] = {
            "hit_rate": report.hit_rate,
            "mrr": report.mrr,
            "cases": len(report.results),
            "misses": [r.case.query for r in report.misses],
        }
        if report.judged:
            report_json["mean_faithfulness"] = report.mean_faithfulness
            report_json["mean_relevancy"] = report.mean_relevancy
        typer.echo(json.dumps(report_json, indent=2))
    else:
        typer.echo(
            f"{len(report.results)} case(s): "
            f"hit_rate={report.hit_rate:.2f} mrr={report.mrr:.2f}"
        )
        if report.judged:
            typer.echo(
                f"  answer quality: faithfulness={report.mean_faithfulness:.2f} "
                f"relevancy={report.mean_relevancy:.2f}"
            )
        for result in report.results:
            status = f"rank {result.rank}" if result.hit else "MISS"
            typer.echo(
                f"  [{status}] {result.case.query!r} -> {result.case.expected_source_uri}"
            )
            if result.judge is not None:
                typer.echo(
                    f"      judge: faithfulness={result.judge.faithfulness:.2f} "
                    f"relevancy={result.judge.relevancy:.2f} — {result.judge.reason}"
                )
    if report.hit_rate < min_hit_rate:
        typer.echo(
            f"hit_rate {report.hit_rate:.2f} below --min-hit-rate {min_hit_rate:.2f}",
            err=True,
        )
        raise typer.Exit(1)
