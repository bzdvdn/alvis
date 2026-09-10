"""``alvis chat`` — interactive multi-turn Q&A over a pipeline's index.

Unlike ``alvis query --answer`` (one question, one process), this keeps a
conversation transcript in memory for the life of the REPL and replays it
into each answer's synthesis prompt — see :class:`alvis.answer.ChatTurn`.
Retrieval for each turn still runs on that turn's own text (no query
rewriting from history), so a follow-up whose relevant terms only appear
in an earlier turn may retrieve the wrong chunks even though the answer
*sounds* aware of the conversation — the model sees the history, the
retriever doesn't.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from alvis._lifecycle import aclose_quietly
from alvis.answer import ChatTurn, Synthesizer
from alvis.cli._query import _parse_filters
from alvis.cli._shared import _enable_plugins, _load_env_file, app
from alvis.config import ConfigError
from alvis.errors import PipelineError
from alvis.pipeline.runner import answer_async
from alvis.rerank import LLMReranker
from alvis.secrets import resolve_secret

_EXIT_COMMANDS = {"exit", "quit"}


async def _chat_loop(
    config: str,
    *,
    top_k: int,
    filters: dict[str, str] | None,
    hybrid: bool,
    reranker: LLMReranker | None,
    principals: list[str] | None,
    llm: Synthesizer | None,
) -> None:
    history: list[ChatTurn] = []
    typer.echo("Alvis chat — ask a question. 'exit' or Ctrl+D to quit.\n")
    try:
        while True:
            try:
                text = input("you> ").strip()
            except EOFError:
                typer.echo()
                break
            if not text:
                continue
            if text.lower() in _EXIT_COMMANDS:
                break
            try:
                result = await answer_async(
                    config,
                    text,
                    top_k=top_k,
                    llm=llm,
                    filters=filters,
                    hybrid=hybrid,
                    rerank=reranker,
                    principals=principals,
                    history=history,
                )
            except (ConfigError, PipelineError) as exc:
                typer.echo(f"Error: {exc}", err=True)
                continue
            typer.echo(f"alvis> {result.text}")
            if result.citations:
                for citation in result.citations:
                    typer.echo(f"  [{citation.index}] {citation.source_uri}")
            typer.echo()
            history.append(ChatTurn(question=text, answer=result.text))
    finally:
        if llm is not None:
            await aclose_quietly(llm)
        if reranker is not None:
            await aclose_quietly(reranker)


@app.command()
def chat(
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
    top_k: int = typer.Option(  # noqa: B008
        5,
        "--top-k",
        help="Number of nearest chunks to retrieve per turn.",
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
        help="Fuse dense search with keyword search on every turn.",
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
    llm_base_url: str = typer.Option(  # noqa: B008
        "https://api.openai.com/v1",
        "--answer-base-url",
        help="OpenAI-compatible chat endpoint used to synthesize each answer.",
    ),
    llm_model: str = typer.Option(  # noqa: B008
        "gpt-4o-mini",
        "--answer-model",
        help="Chat model used to synthesize each answer.",
    ),
    llm_api_token_env: str = typer.Option(  # noqa: B008
        "OPENAI_API_KEY",
        "--answer-api-token-env",
        help="Env var holding the chat API token. Without it, falls back to "
        "numbered excerpts each turn (no conversation memory in that mode).",
    ),
) -> None:
    """Interactive multi-turn Q&A: each answer sees the prior turns of this session."""
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
    reranker = (
        LLMReranker(
            base_url=rerank_base_url, model=rerank_model, api_token_env=rerank_api_token_env
        )
        if rerank
        else None
    )
    llm = None
    if resolve_secret(llm_api_token_env):
        llm = Synthesizer(
            base_url=llm_base_url, model=llm_model, api_token_env=llm_api_token_env
        )
    else:
        typer.echo(
            f"Warning: {llm_api_token_env} is not set; falling back to numbered "
            "excerpts each turn (no conversation memory in that mode).",
            err=True,
        )
    asyncio.run(
        _chat_loop(
            config,
            top_k=top_k,
            filters=filters,
            hybrid=hybrid,
            reranker=reranker,
            principals=principal,
            llm=llm,
        )
    )
