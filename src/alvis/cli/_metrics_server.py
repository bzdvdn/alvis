"""Shared Prometheus ``/metrics`` HTTP server, used by ``run --metrics-port``
and the standalone ``metrics`` command."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import typer

import alvis.observability as ob


class _MetricsHandler(BaseHTTPRequestHandler):
    """Serves the process-wide metric store as Prometheus text exposition."""

    def do_GET(self) -> None:
        if self.path.rstrip("/") != "/metrics":
            self.send_error(404)
            return
        store = ob.get_metrics()
        try:
            body = store.export_prometheus()
        except Exception:  # noqa: BLE001 — prometheus_client missing
            body = b""
        if not body:
            body = store.render_plain_text()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        ob.LOG.debug(
            "metrics.http",
            extra={"message": " ".join((format, *map(str, args)))},
        )


def _start_metrics_server(
    host: str | None,
    port: int | None,
) -> ThreadingHTTPServer | None:
    """Start /metrics on a daemon thread, or ``None`` when ``port`` is unset."""
    if port is None:
        return None
    server = ThreadingHTTPServer((host or "127.0.0.1", port), _MetricsHandler)

    def _serve() -> None:
        server.serve_forever()

    threading.Thread(target=_serve, daemon=True).start()
    typer.echo(f"Serving /metrics on http://{host or '127.0.0.1'}:{port}")
    return server


__all__ = ["_start_metrics_server"]
