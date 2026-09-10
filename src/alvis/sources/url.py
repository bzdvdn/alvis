"""Static URL source — ingests plain HTML pages served over HTTP(S).

Designed for static sites and classic docs pages that render server-side
(no JavaScript needed). Pages are fingerprinted with HTTP validators
(``ETag`` / ``Last-Modified``) during cheap listing, so ``alvis run``
downloads a body only when the page actually changed — incremental by
default. Servers that answer ``HEAD`` with no validators — or refuse
``HEAD`` altogether — fall back to a ``GET`` and a content hash.

Config keys: ``urls`` (explicit list of page URLs to ingest), ``timeout``,
``retries``, ``retry_backoff``, ``verify``, ``api_token_env`` (sent as a
``Bearer`` token), ``max_bytes`` (per-page byte cap; larger pages are
skipped). The page's own ``Content-Type`` wins when it names a text type;
clean URLs without an extension are treated as ``text/html``.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from urllib.parse import urlparse

import httpx

from alvis._concurrency import gather_bounded
from alvis.core.models import Artifact, DocumentMeta
from alvis.secrets import resolve_secret
from alvis.sources.base import SourceError
from alvis.sources.content_types import content_type
from alvis.transport import RETRYABLE_STATUS, backoff_delay

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_TAGS_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

#: Servers that reject ``HEAD`` (or serve it inconsistently) fall back to GET.
_HEAD_FALLBACK_STATUS = {400, 403, 405, 406, 501}


class StaticUrlSource:
    """Yields one artifact per URL listed in ``urls``.

    Implements cheap listing: :meth:`list_documents` fingerprints each page
    from its ``ETag`` or ``Last-Modified`` validator via a ``HEAD`` request,
    so unchanged pages are never downloaded again. Pages that return 404/410
    disappear from the listing and are pruned by the index reconcile pass.
    """

    def __init__(
        self,
        urls: list[str],
        *,
        api_token_env: str | None = None,
        timeout: float = 20.0,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
        max_bytes: int | None = None,
        max_concurrency: int = 8,
    ) -> None:
        if not urls:
            raise SourceError("static_url requires at least one 'urls' entry")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.max_concurrency = max_concurrency
        self.urls = list(urls)
        self.api_token_env = api_token_env
        self.timeout = timeout
        self.max_retries = retries
        self.retry_backoff = retry_backoff
        self.verify = verify
        self.transport = transport
        self.max_bytes = max_bytes
        self._client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool, if one was ever opened."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def list_documents(self) -> list[DocumentMeta]:
        """Fingerprint every URL without downloading unchanged bodies.

        One ``HEAD`` (or fallback ``GET``) per URL, run concurrently,
        bounded by ``max_concurrency``.
        """
        metas = await gather_bounded(
            self.urls, self._meta, max_concurrency=self.max_concurrency
        )
        return [meta for meta in metas if meta is not None]

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        """Download the wanted URLs (or all when ``uris`` is ``None``).

        Downloads run concurrently, bounded by ``max_concurrency``.
        """
        wanted = [url for url in self.urls if uris is None or url in uris]

        async def _fetch_one(url: str) -> Artifact | None:
            try:
                return await self._artifact(await self._get(url), url)
            except SourceError as exc:
                if exc.status_code == 413:
                    return None  # oversized page skipped, others abort the run
                raise

        results = await gather_bounded(
            wanted, _fetch_one, max_concurrency=self.max_concurrency
        )
        return [artifact for artifact in results if artifact is not None]

    async def _meta(self, url: str) -> DocumentMeta | None:
        """Build one listing entry from HTTP validators (GET+hash fallback)."""
        response: httpx.Response | None = None
        try:
            response = await self._request("HEAD", url, ok=(200, 204))
        except SourceError as exc:
            if exc.status_code in _HEAD_FALLBACK_STATUS or exc.status_code == 413:
                pass  # fall through to GET + hash
            elif exc.status_code in {404, 410}:
                return None  # gone; the indexer's reconcile prunes stale points
            else:
                raise

        fingerprint: str | None = None
        if response is not None:
            fingerprint = response.headers.get("etag") or response.headers.get(
                "last-modified"
            )
        if fingerprint is None:
            try:
                body = await self._read_capped(await self._get(url), url)
            except SourceError as exc:
                if exc.status_code in {404, 410}:
                    return None
                if exc.status_code == 413:
                    return None  # exceeds the byte cap; cannot fingerprint at all
                raise
            fingerprint = hashlib.sha256(body).hexdigest()
        return DocumentMeta(
            uri=url,
            step_id=url,
            fingerprint=fingerprint,
            content_type=_content_type(url, response),
        )

    async def _artifact(self, response: httpx.Response, url: str) -> Artifact:
        data = await self._read_capped(response, url)
        return Artifact(
            step_id=url,
            uri=url,
            content_type=_content_type(url, response),
            data=data,
            metadata={
                "path": url,
                "sourceUrl": url,
                "title": _page_title(data, url),
            },
        )

    async def _get(self, url: str) -> httpx.Response:
        return await self._request("GET", url, ok=(200,))

    async def _request(
        self,
        method: str,
        url: str,
        *,
        ok: tuple[int, ...],
    ) -> httpx.Response:
        """Perform an idempotent request with retry/backoff (redirect-following)."""
        client = self._client or httpx.AsyncClient(
            transport=self.transport,
            verify=self.verify,
            follow_redirects=True,
        )
        self._client = client
        for attempt in range(self.max_retries + 1):
            try:
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
            except httpx.RequestError as exc:
                if attempt >= self.max_retries:
                    raise SourceError(
                        f"{method} {url} failed after {self.max_retries} retries: {exc}"
                    ) from exc
                await asyncio.sleep(backoff_delay(attempt, self.retry_backoff))
                continue

            if response.status_code in RETRYABLE_STATUS:
                if attempt >= self.max_retries:
                    raise SourceError(
                        f"{method} {url} returned HTTP {response.status_code} "
                        f"after {self.max_retries} retries",
                        status_code=response.status_code,
                    )
                await asyncio.sleep(_retry_delay(attempt, self.retry_backoff, response))
                continue

            if response.status_code not in ok:
                raise SourceError(
                    f"{method} {url} returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            return response
        raise SourceError(f"{method} {url} failed")  # pragma: no cover

    async def _read_capped(self, response: httpx.Response, url: str) -> bytes:
        """Read the body, aborting once the configured byte cap is exceeded."""
        if self.max_bytes is None:
            return await response.aread()
        parts = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=65536):
            parts.extend(chunk)
            if len(parts) > self.max_bytes:
                raise SourceError(
                    f"GET {url} exceeds max_bytes={self.max_bytes}",
                    status_code=413,
                )
        return bytes(parts)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "text/html, text/plain, application/json, */*"}
        if self.api_token_env:
            token = resolve_secret(self.api_token_env)
            if not token:
                raise SourceError(
                    f"secret {self.api_token_env!r} is not set "
                    f"(configured via api_token_env)"
                )
            headers["Authorization"] = f"Bearer {token}"
        return headers


def _page_title(data: bytes, url: str) -> str:
    match = _TITLE_RE.search(data.decode("utf-8", errors="replace"))
    if match is not None:
        title = _WS_RE.sub(" ", _TAGS_RE.sub(" ", match.group(1))).strip()
        if title:
            return title
    parsed = urlparse(url)
    return (f"{parsed.netloc}{parsed.path}".rstrip("/")) or parsed.netloc


def _content_type(url: str, response: httpx.Response | None) -> str:
    """Pick a Alvis content type for the page.

    The server's ``Content-Type`` header wins when it names an understood
    text type; otherwise the URL's extension decides, defaulting to
    ``text/html`` for clean URLs (no extension).
    """
    if response is not None:
        header = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if header in {"text/html", "application/xhtml+xml"}:
            return "text/html"
        if header in {"text/markdown", "text/x-markdown"}:
            return "text/markdown"
        if header == "application/json":
            return "application/json"
    path = urlparse(url).path
    fallback = content_type(path)
    if fallback == "text/plain" and not _path_suffix(path):
        return "text/html"
    return fallback


def _path_suffix(path: str) -> bool:
    return "." in path.rsplit("/", 1)[-1]


def _retry_delay(
    attempt: int, retry_backoff: float, response: httpx.Response
) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            import email.utils

            parsed = email.utils.parsedate(retry_after)
            if parsed:
                return max(0.0, time.mktime(parsed) - time.time())
    return backoff_delay(attempt, retry_backoff)