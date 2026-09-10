"""``alvis eval`` — score retrieval quality against a fixed set of cases."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from alvis._lifecycle import aclose_quietly
from alvis.cli._shared import _enable_plugins, _load_env_file, app
from alvis.config import ConfigError
from alvis.errors import PipelineError
from alvis.evaluation import EvalCase, EvalReport, evaluate_async, load_cases
from alvis.rerank import LLMReranker
from alvis.secrets import resolve_secret


async def _evaluate_and_close(
    config: str,
    cases: list[EvalCase],
    top_k: int,
    hybrid: bool,
    reranker: LLMReranker | None = None,
    principals: list[str] | None = None,
) -> EvalReport:
    """``evaluate_async`` plus closing the CLI-owned reranker client."""
    try:
        return await evaluate_async(
            config, cases, top_k=top_k, hybrid=hybrid, rerank=reranker, principals=principals
        )
    finally:
        if reranker is not None:
            await aclose_quietly(reranker)


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
    reranker = (
        LLMReranker(
            base_url=rerank_base_url, model=rerank_model, api_token_env=rerank_api_token_env
        )
        if rerank
        else None
    )
    try:
        eval_cases = load_cases(cases)
        report = asyncio.run(
            _evaluate_and_close(config, eval_cases, top_k, hybrid, reranker, principal)
        )
    except (ConfigError, PipelineError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "hit_rate": report.hit_rate,
                    "mrr": report.mrr,
                    "cases": len(report.results),
                    "misses": [r.case.query for r in report.misses],
                },
                indent=2,
            )
        )
    else:
        typer.echo(
            f"{len(report.results)} case(s): "
            f"hit_rate={report.hit_rate:.2f} mrr={report.mrr:.2f}"
        )
        for result in report.results:
            status = f"rank {result.rank}" if result.hit else "MISS"
            typer.echo(
                f"  [{status}] {result.case.query!r} -> {result.case.expected_source_uri}"
            )
    if report.hit_rate < min_hit_rate:
        typer.echo(
            f"hit_rate {report.hit_rate:.2f} below --min-hit-rate {min_hit_rate:.2f}",
            err=True,
        )
        raise typer.Exit(1)
