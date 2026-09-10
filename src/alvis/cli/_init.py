"""``alvis init`` — scaffold a runnable pipeline YAML from source/index templates."""

from __future__ import annotations

from pathlib import Path

import typer
import yaml

from alvis.cli._shared import app

_SOURCE_TEMPLATES: dict[str, dict[str, object]] = {
    "fs": {"type": "fs", "config": {"path": "./data"}},
    "confluence": {
        "type": "confluence",
        "config": {
            "url": "https://wiki.example.com",
            "space": "TEAM",
            "api_token_env": "CONFLUENCE_API_TOKEN",
        },
    },
    "github": {
        "type": "github",
        "config": {
            "repo": "org/repo",
            "branch": "main",
            "api_token_env": "GITHUB_TOKEN",
        },
    },
    "gitlab": {
        "type": "gitlab",
        "config": {
            "url": "https://gitlab.com",
            "project": "org/repo",
            "api_token_env": "GITLAB_TOKEN",
        },
    },
    "s3": {
        "type": "s3",
        "config": {
            "url": "http://localhost:9000",
            "bucket": "docs",
            "region": "us-east-1",
            "access_key_env": "S3_ACCESS_KEY",
            "secret_key_env": "S3_SECRET_KEY",
        },
    },
    "static_url": {
        "type": "static_url",
        "config": {
            "urls": ["https://example.com/docs/"],
        },
    },
}

_INDEX_TEMPLATES: dict[str, dict[str, object]] = {
    "memory": {"type": "memory", "config": {}},
    "sqlite": {"type": "sqlite", "config": {"path": "alvis.db"}},
    "qdrant": {
        "type": "qdrant",
        "config": {"url": "http://localhost:6333", "collection": "alvis_docs"},
    },
    "pgvector": {
        "type": "pgvector",
        "config": {"dsn_env": "POSTGRES_DSN", "table": "alvis_chunks"},
    },
    "elasticsearch": {
        "type": "elasticsearch",
        "config": {"url": "http://localhost:9200", "index": "alvis_docs"},
    },
}


def _pipeline_yaml(source: str, index: str) -> str:
    """Render a runnable pipeline from the ``--source`` / ``--index`` templates."""
    if source not in _SOURCE_TEMPLATES:
        raise typer.BadParameter(
            f"unknown source {source!r} (known: {sorted(_SOURCE_TEMPLATES)})"
        )
    if index not in _INDEX_TEMPLATES:
        raise typer.BadParameter(
            f"unknown index {index!r} (known: {sorted(_INDEX_TEMPLATES)})"
        )
    pipeline = {
        "pipeline": {
            "source": _SOURCE_TEMPLATES[source],
            "extract": {"strategy": "auto"},
            "chunk": {
                "strategy": "auto",
                "config": {"max_tokens": 500, "overlap": 50},
            },
            "embed": {"type": "default"},
            "index": _INDEX_TEMPLATES[index],
        }
    }
    return yaml.safe_dump(pipeline, sort_keys=False)


@app.command()
def init(
    source: str = typer.Option(  # noqa: B008
        "confluence",
        "--source",
        help="Source adapter to scaffold (fs, confluence, github, gitlab, s3, static_url).",
    ),
    index: str = typer.Option(  # noqa: B008
        "qdrant",
        "--index",
        help="Index adapter to scaffold (memory, sqlite, qdrant, pgvector, elasticsearch).",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing alvis.yaml."),
    path: Path = typer.Argument(  # noqa: B008
        Path("alvis.yaml"), help="Where to write the pipeline config."
    ),
) -> None:
    """Scaffold a new pipeline project (source/index-specific template)."""
    if path.exists() and not force:
        typer.echo(f"Error: {path} already exists. Use --force to overwrite.", err=True)
        raise typer.Exit(1)
    path.write_text(_pipeline_yaml(source, index), encoding="utf-8")
    typer.echo(f"Created {path} ({source} source, {index} index)")
    typer.echo(
        f"Next: edit {path}, set its env vars (or a .env), "
        "then run 'alvis validate {path}'"
    )
