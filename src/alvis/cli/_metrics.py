"""``alvis metrics`` — serve Prometheus metrics standalone (GET /metrics)."""

from __future__ import annotations

import typer

from alvis.cli._metrics_server import _start_metrics_server
from alvis.cli._shared import app


@app.command()
def metrics(
    host: str = typer.Option(  # noqa: B008
        "127.0.0.1",
        "--host",
        help="Interface to bind (use 0.0.0.0 inside containers).",
    ),
    port: int = typer.Option(  # noqa: B008
        8000,
        "--port",
        help="TCP port to serve /metrics on.",
    ),
) -> None:
    """Expose Prometheus metrics over HTTP (GET /metrics)."""
    server = _start_metrics_server(host, port)
    if server is None:
        raise typer.Exit(1)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("\nStopped.")
