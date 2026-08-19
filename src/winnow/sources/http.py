"""Small HTTP helpers for adapters that talk to REST APIs."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from collections.abc import Callable
from typing import Any, Literal, overload

import httpx

from winnow.sources.base import SourceError
from winnow.transport import RETRYABLE_STATUS, backoff_delay


class HttpClient:
    """A thin JSON client shared by REST adapters (httpx + async).

    Transient failures are retried with exponential backoff and jitter:
    connection errors and status codes in ``RETRYABLE_STATUS`` (429, 5xx).
    ``Retry-After`` is honored when present. All requests Winnow issues are
    idempotent, so retrying is safe.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_token_env: str | None = None,
        username: str | None = None,
        timeout: float = 30.0,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
        header_hook: Callable[[str, str, dict[str, Any] | None], dict[str, str]] | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token_env = api_token_env
        self.username = username
        self.timeout = timeout
        self.max_retries = retries
        self.retry_backoff = retry_backoff
        self.verify = verify
        self.transport = transport
        self.header_hook = header_hook
        self.max_bytes = max_bytes

    def _headers(self, method: str, path: str, query: dict[str, Any] | None) -> dict[str, str]:
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
        if self.header_hook:
            headers.update(self.header_hook(method, path, query))
        return headers

    @overload
    async def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        ok_status: tuple[int, ...] = (200, 201),
        raw: Literal[False] = False,
    ) -> dict[str, Any]: ...

    @overload
    async def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        ok_status: tuple[int, ...] = (200, 201),
        raw: Literal[True],
    ) -> bytes: ...

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        ok_status: tuple[int, ...] = (200, 201),
        raw: bool = False,
    ) -> dict[str, Any] | bytes:
        url = self.base_url + path
        async with self._client() as client:
            for attempt in range(self.max_retries + 1):
                try:
                    resp = await client.request(
                        method,
                        url,
                        params=query,
                        json=payload,
                        headers=self._headers(method, path, query),
                        timeout=self.timeout,
                    )
                except httpx.RequestError as exc:
                    if attempt >= self.max_retries:
                        raise SourceError(
                            f"{method} {url} failed after {self.max_retries} retries: {exc}"
                        ) from exc
                    await asyncio.sleep(self._delay(attempt, None, None))
                    continue

                if resp.status_code in RETRYABLE_STATUS:
                    if attempt >= self.max_retries:
                        raise SourceError(
                            f"{method} {url} returned HTTP {resp.status_code} "
                            f"after {self.max_retries} retries",
                            status_code=resp.status_code,
                        )
                    await asyncio.sleep(
                        self._delay(
                            attempt,
                            resp.status_code,
                            resp.headers.get("Retry-After"),
                        )
                    )
                    continue

                if resp.status_code not in ok_status:
                    raise SourceError(
                        f"{method} {url} returned HTTP {resp.status_code}",
                        status_code=resp.status_code,
                    )
                body = await self._read_capped(resp, method, url)
                if raw:
                    return body
                return json.loads(body) if body else {}

        return b"" if raw else {}

    async def _read_capped(
        self,
        resp: httpx.Response,
        method: str,
        url: str,
    ) -> bytes:
        """Read the response body, aborting once ``max_bytes`` is exceeded.

        The body is consumed incrementally (``aiter_bytes``), so a response
        larger than the configured budget never materialises in memory. When
        no cap is set the body is read as a whole.
        """
        if self.max_bytes is None:
            return await resp.aread()
        parts = bytearray()
        async for chunk in resp.aiter_bytes(chunk_size=65536):
            parts.extend(chunk)
            if len(parts) > self.max_bytes:
                raise SourceError(
                    f"{method} {url} exceeds max_bytes={self.max_bytes}",
                    status_code=413,
                )
        return bytes(parts)

    def _delay(self, attempt: int, status: int | None, retry_after: str | None) -> float:
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                import email.utils

                parsed = email.utils.parsedate(retry_after)
                if parsed:
                    return max(0.0, time.mktime(parsed) - time.time())
        return backoff_delay(attempt, self.retry_backoff)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport, verify=self.verify)