"""``alvis query`` — retrieve (or synthesize a cited answer over) the closest chunks."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from alvis._lifecycle import aclose_quietly
from alvis.answer import Answer, Synthesizer
from alvis.cli._shared import _enable_plugins, _load_env_file, app
from alvis.config import ConfigError
from alvis.core.models import SearchHit
from alvis.errors import PipelineError
from alvis.pipeline.runner import answer_async, query_async
from alvis.rerank import LLMReranker
from alvis.secrets import resolve_secret


async def _answer_and_close(
    config: str,
    text: str,
    top_k: int,
    llm: Synthesizer | None,
    filters: dict[str, str] | None,
    hybrid: bool,
    reranker: LLMReranker | None = None,
    principals: list[str] | None = None,
) -> Answer:
    """``answer_async`` plus closing the CLI-owned LLM clients."""
    try:
        return await answer_async(
            config,
            text,
            top_k=top_k,
            llm=llm,
            filters=filters,
            hybrid=hybrid,
            rerank=reranker,
            principals=principals,
        )
    finally:
        if llm is not None:
            await aclose_quietly(llm)
        if reranker is not None:
            await aclose_quietly(reranker)


async def _query_and_close(
    config: str,
    text: str,
    top_k: int,
    filters: dict[str, str] | None,
    hybrid: bool,
    reranker: LLMReranker | None = None,
    principals: list[str] | None = None,
) -> list[SearchHit]:
    """``query_async`` plus closing the CLI-owned reranker client."""
    try:
        return await query_async(
            config,
            text,
            top_k=top_k,
            filters=filters,
            hybrid=hybrid,
            rerank=reranker,
            principals=principals,
        )
    finally:
        if reranker is not None:
            await aclose_quietly(reranker)


def _parse_filters(pairs: list[str] | None) -> dict[str, str] | None:
    """Parse repeated ``--filter key=value`` options into a metadata filter dict."""
    if not pairs:
        return None
    filters: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"--filter expects key=value, got {pair!r}")
        filters[key] = value
    return filters


@app.command()
def query(
    config: str = typer.Argument(  # noqa: B008
        ...,
        help="Path to the pipeline YAML config (uses its embed + index stages).",
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
    text: str = typer.Option(  # noqa: B008
        ...,
        "--text",
        "-t",
        help="Query text to embed and search for.",
    ),
    top_k: int = typer.Option(  # noqa: B008
        5,
        "--top-k",
        help="Number of nearest chunks to return.",
    ),
    filter_pairs: list[str] = typer.Option(  # noqa: B008
        None,
        "--filter",
        help="Restrict to metadata key=value (repeatable; all pairs must match).",
    ),
    principal: list[str] = typer.Option(  # noqa: B008
        None,
        "--principal",
        help=(
            "Caller identity (repeatable) for ACL enforcement: excludes chunks "
            "whose 'acl' doesn't include any of these. Omit for no ACL filtering."
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
    answer: bool = typer.Option(  # noqa: B008
        False,
        "--answer",
        "-a",
        help="Synthesize a cited answer over the hits (LLM optional).",
    ),
    llm_base_url: str = typer.Option(  # noqa: B008
        "https://api.openai.com/v1",
        "--answer-base-url",
        help="OpenAI-compatible chat endpoint for --answer.",
    ),
    llm_model: str = typer.Option(  # noqa: B008
        "gpt-4o-mini",
        "--answer-model",
        help="Chat model for --answer.",
    ),
    llm_api_token_env: str = typer.Option(  # noqa: B008
        "OPENAI_API_KEY",
        "--answer-api-token-env",
        help="Env var holding the chat API token for --answer.",
    ),
) -> None:
    """Retrieve the chunks closest to --text (or synthesize an answer)."""
    _enable_plugins(plugin_dirs)
    _load_env_file(env_file)
    if rerank and not resolve_secret(rerank_api_token_env):
        typer.echo(
            f"Warning: --rerank set but {rerank_api_token_env} is not set; "
            "skipping reranking.",
            err=True,
        )
        rerank = False
    try:
        filters = _parse_filters(filter_pairs)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    reranker = None
    if rerank:
        reranker = LLMReranker(
            base_url=rerank_base_url,
            model=rerank_model,
            api_token_env=rerank_api_token_env,
        )
    try:
        if answer:
            llm = None
            if resolve_secret(llm_api_token_env):
                llm = Synthesizer(
                    base_url=llm_base_url,
                    model=llm_model,
                    api_token_env=llm_api_token_env,
                )
            result = asyncio.run(
                _answer_and_close(
                    config, text, top_k, llm, filters, hybrid, reranker, principal
                )
            )
        else:
            results = asyncio.run(
                _query_and_close(config, text, top_k, filters, hybrid, reranker, principal)
            )
            result = None
    except (ConfigError, PipelineError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if result is not None:
        typer.echo(result.text)
        typer.echo("")
        if result.citations:
            typer.echo("Sources:")
            for citation in result.citations:
                typer.echo(
                    f"  [{citation.index}] {citation.source_uri}  "
                    f"(score {citation.score:.4f})"
                )
        return
    if not results:
        typer.echo("No matches found.")
        return
    for index, hit in enumerate(results, start=1):
        typer.echo(f"[{index}] {hit.score:.4f}  {hit.source_uri}")
        for key, value in hit.metadata.items():
            if value:
                typer.echo(f"      {key}: {value}")
        snippet = " ".join(hit.text.split())
        typer.echo(f"      {snippet[:180]}")
