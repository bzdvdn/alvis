"""Small HTTP helpers for adapters that talk to REST APIs."""

from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from winnow.sources.base import SourceError


class HttpClient:
    """A thin JSON client shared by REST adapters (httpx + async)."""

    def __init__(
        self,
        base_url: str,
        *,
        api_token_env: str | None = None,
        username: str | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token_env = api_token_env
        self.username = username
        self.timeout = timeout
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_token_env:
            token = os.environ.get(self.api_token_env)
            if not token:
                raise SourceError(
                    f"environment variable {self.api_token_env!r} is not set "
                    f"(configured via api_token_env)"
                )
            if self.username:
                credentials = base64.b64encode(
                    f"{self.username}:{token}".encode()
                ).decode()
                headers["Authorization"] = f"Basic {credentials}"
            else:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        ok_status: tuple[int, ...] = (200, 201),
    ) -> dict[str, Any]:
        url = self.base_url + path
        async with self._client() as client:
            try:
                resp = await client.request(
                    method,
                    url,
                    params=query,
                    json=payload,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
            except httpx.RequestError as exc:
                raise SourceError(f"{method} {url} failed: {exc}") from exc
            if resp.status_code not in ok_status:
                raise SourceError(
                    f"{method} {url} returned HTTP {resp.status_code}",
                    status_code=resp.status_code,
                )
            return resp.json() if resp.content else {}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport)