"""Test-support harness for connector authors (fixture + golden snapshots).

This module is the "connector fixture" promised by the roadmap: a small,
dependency-free toolkit for writing connector tests without standing up a
real service. It ships with the package so external connector repositories
can reuse it directly. Two pieces:

- :class:`MockServer` — a route-based fake HTTP server built on
  ``httpx.MockTransport``. Point a connector's ``transport`` at it, define
  canned responses, and assert on the recorded requests.
- :func:`assert_golden` — snapshot testing. A connector ships one golden
  snapshot per behaviour; ``ALVIS_ACCEPT=1`` (re)writes snapshots, CI runs
  them read-only so drift fails loudly.

Everything here is stdlib + ``httpx`` (already a runtime dependency).
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

import httpx

from alvis.core.models import Artifact

DEFAULT_GOLDEN_DIR = Path(__file__).resolve().parents[2] / "tests" / "golden"
"""Default location of committed golden snapshots (in-repo tests)."""

GOLDEN_MARKER = "golden"
"""pytest marker name used to select connector golden tests."""


def _golden_dir() -> Path:
    return Path(os.environ.get("ALVIS_GOLDEN_DIR", str(DEFAULT_GOLDEN_DIR)))


class MockServer:
    """A tiny route-based HTTP mock for connector tests.

    Register canned responses with :meth:`on`, then hand a connector the
    :attr:`transport` property. Every request is recorded so tests can
    assert on the exact calls a connector makes.
    """

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], httpx.Response] = {}
        self.requests: list[httpx.Request] = []

    def on(
        self,
        method: str,
        path: str,
        *,
        status: int = 200,
        json_payload: object | None = None,
        content: bytes | None = None,
    ) -> None:
        """Route ``method`` + ``path`` to a canned response."""
        if json_payload is not None:
            response = httpx.Response(status, json=json_payload)
        else:
            response = httpx.Response(status, content=content)
        self._routes[(method.upper(), path)] = response

    def _path(self, request: httpx.Request) -> str:
        return request.url.raw_path.split(b"?", 1)[0].decode()

    def handler(self, request: httpx.Request) -> httpx.Response:
        """``httpx.MockTransport`` handler; raises on unmapped routes."""
        self.requests.append(request)
        path = self._path(request)
        response = self._routes.get((request.method, path))
        if response is None:
            raise AssertionError(
                f"no route for {request.method} {path} "
                f"(registered: {sorted(self._routes)})"
            )
        return response

    @property
    def transport(self) -> httpx.MockTransport:
        """A transport the connector's ``HttpClient`` can use."""
        return httpx.MockTransport(self.handler)

    def request_log(self) -> list[dict[str, object]]:
        """Recorded requests as ``{method, path, query}`` dicts."""
        return [
            {
                "method": request.method,
                "path": self._path(request),
                "query": dict(request.url.params.items()),
            }
            for request in self.requests
        ]


def artifacts_snapshot(
    artifacts: Sequence[Artifact],
    *,
    root: str | None = None,
) -> list[dict[str, object]]:
    """Deterministic, serialisable snapshot of a ``fetch()`` result.

    ``root`` (an absolute path that varies per machine, e.g. a test corpus
    directory) is replaced with ``<root>`` so snapshots stay portable.
    """
    def scrub(value: str) -> str:
        return value.replace(root, "<root>") if root else value

    return [
        {
            "step_id": scrub(artifact.step_id),
            "uri": scrub(artifact.uri),
            "content_type": artifact.content_type,
            "data": artifact.data.decode("utf-8", errors="replace"),
            "metadata": {
                key: scrub(value) if isinstance(value, str) else value
                for key, value in artifact.metadata.items()
            },
        }
        for artifact in artifacts
    ]


def assert_golden(name: str, actual: object) -> None:
    """Compare ``actual`` against a committed golden snapshot.

    With ``ALVIS_ACCEPT=1`` the snapshot is (re)written instead of compared.
    Raises ``AssertionError`` on a missing snapshot or drift — use this in
    tests marked ``@pytest.mark.golden``.
    """
    target = _golden_dir() / f"{name}.golden.json"
    payload = json.dumps(actual, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if os.environ.get("ALVIS_ACCEPT"):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
        return
    if not target.exists():
        raise AssertionError(
            f"golden snapshot missing: {target} — run with ALVIS_ACCEPT=1 to create it"
        )
    if target.read_text(encoding="utf-8") != payload:
        raise AssertionError(
            f"golden drift in {target}: output changed — review, then ALVIS_ACCEPT=1"
        )


__all__ = [
    "DEFAULT_GOLDEN_DIR",
    "GOLDEN_MARKER",
    "MockServer",
    "artifacts_snapshot",
    "assert_golden",
]
