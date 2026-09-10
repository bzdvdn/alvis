"""SharePoint source — fetches files from a site's document library via Microsoft Graph.

Authenticates with an OAuth2 client-credentials grant against Azure AD (an
app registration with an admin-consented ``Sites.Read.All`` — or
site-specific — Microsoft Graph application permission), then walks the
site's default drive via Graph's delta query. No Microsoft Graph SDK
dependency: plain REST via :class:`alvis.sources.http.HttpClient`, same as
every other source here — the one addition this needed was
``HttpClient``'s new ``form`` request body (OAuth2 token exchanges are
``application/x-www-form-urlencoded``, not JSON).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from alvis._concurrency import gather_bounded
from alvis.core.models import Artifact, DocumentMeta
from alvis.secrets import resolve_secret
from alvis.sources.base import SourceError
from alvis.sources.content_types import content_type, is_ingestible, matches_globs
from alvis.sources.http import HttpClient

_TOKEN_HOST = "https://login.microsoftonline.com"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_EXPIRY_SAFETY_MARGIN = 60.0
"""Refresh the access token this many seconds before Graph would consider
it expired, so a token that's about to expire mid-request gets renewed
instead of used."""


def _parse_site_url(site_url: str) -> tuple[str, str]:
    parsed = urlsplit(site_url)
    return parsed.netloc, (parsed.path.rstrip("/") or "/")


def _relative_path(item: dict[str, Any]) -> str:
    """Reconstruct ``folder/name`` from a driveItem's ``parentReference.path``
    (Graph returns paths like ``/drive/root:/Folder/Sub``)."""
    parent_path = str(item.get("parentReference", {}).get("path", ""))
    marker = "root:"
    index = parent_path.find(marker)
    folder = parent_path[index + len(marker) :].lstrip("/") if index != -1 else ""
    name = str(item.get("name", ""))
    return f"{folder}/{name}" if folder else name


class SharePointSource:
    """Yields one artifact per ingestible file in a site's default document library.

    Config keys: ``tenant_id`` (Azure AD tenant), ``client_id`` (app
    registration), ``client_secret_env`` (env var holding the app's client
    secret), ``site_url`` (e.g.
    ``https://contoso.sharepoint.com/sites/TeamSite``), ``include_globs``/
    ``exclude_globs`` (matched against each file's path within the
    library), ``max_bytes``, ``max_concurrency``.
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        site_url: str,
        client_secret_env: str,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        max_bytes: int | None = None,
        max_concurrency: int = 8,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.max_concurrency = max_concurrency
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret_env = client_secret_env
        self.include_globs = include_globs
        self.exclude_globs = exclude_globs
        self.max_bytes = max_bytes
        self.site_hostname, self.site_path = _parse_site_url(site_url)
        self._verify = verify
        self._token_client = HttpClient(
            base_url=_TOKEN_HOST, retries=retries, retry_backoff=retry_backoff, verify=verify
        )
        self.client = HttpClient(
            base_url=_GRAPH_BASE,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
            header_hook=lambda method, path, query: {
                "Authorization": f"Bearer {self._access_token}"
            },
        )
        self._access_token: str | None = None
        self._token_expiry: float = 0.0
        self._token_lock = asyncio.Lock()
        self._drive_id: str | None = None
        self._download_client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        """Release every underlying HTTP connection pool this source opened."""
        await self._token_client.aclose()
        await self.client.aclose()
        if self._download_client is not None:
            await self._download_client.aclose()
            self._download_client = None

    async def list_documents(self) -> list[DocumentMeta]:
        """Fingerprint files by their Graph ``eTag`` (falls back to
        last-modified time) — cheap, no content download."""
        items = await self._list_files()
        return [
            DocumentMeta(
                uri=str(item["webUrl"]),
                step_id=str(item["id"]),
                fingerprint=str(item.get("eTag") or item.get("lastModifiedDateTime", "")),
                content_type=content_type(str(item["name"])),
            )
            for item in items
        ]

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        """Download the wanted files as artifacts.

        With ``uris``, only the given ``webUrl``s are downloaded. Downloads
        run concurrently, bounded by ``max_concurrency``.
        """
        items = await self._list_files()
        wanted = [item for item in items if uris is None or item["webUrl"] in uris]

        async def _fetch_one(item: dict[str, Any]) -> Artifact | None:
            drive_id = await self._ensure_drive()
            detail = await self._graph("GET", f"/drives/{drive_id}/items/{item['id']}")
            download_url = detail.get("@microsoft.graph.downloadUrl")
            if not download_url:
                return None
            data = await self._download(str(download_url))
            if self.max_bytes is not None and len(data) > self.max_bytes:
                return None
            return Artifact(
                step_id=str(item["id"]),
                uri=str(item["webUrl"]),
                content_type=content_type(str(item["name"])),
                data=data,
                metadata={
                    "path": item.get("_relative_path", item["name"]),
                    "title": str(item["name"]),
                    "documentId": str(item["id"]),
                },
            )

        results = await gather_bounded(
            wanted, _fetch_one, max_concurrency=self.max_concurrency
        )
        return [artifact for artifact in results if artifact is not None]

    async def _ensure_token(self) -> None:
        async with self._token_lock:
            if self._access_token and time.monotonic() < self._token_expiry:
                return
            secret = resolve_secret(self.client_secret_env)
            if not secret:
                raise SourceError(
                    f"secret {self.client_secret_env!r} is not set "
                    f"(configured via client_secret_env)"
                )
            response = await self._token_client.request(
                "POST",
                f"/{self.tenant_id}/oauth2/v2.0/token",
                form={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            self._access_token = str(response["access_token"])
            expires_in = float(response.get("expires_in", 3600))
            self._token_expiry = time.monotonic() + expires_in - _TOKEN_EXPIRY_SAFETY_MARGIN

    async def _graph(
        self, method: str, path: str, *, query: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        await self._ensure_token()
        return await self.client.request(method, path, query=query)

    async def _ensure_drive(self) -> str:
        if self._drive_id is not None:
            return self._drive_id
        site = await self._graph("GET", f"/sites/{self.site_hostname}:{self.site_path}")
        drive = await self._graph("GET", f"/sites/{site['id']}/drive")
        self._drive_id = str(drive["id"])
        return self._drive_id

    async def _list_files(self) -> list[dict[str, Any]]:
        drive_id = await self._ensure_drive()
        items: list[dict[str, Any]] = []
        path = f"/drives/{drive_id}/root/delta"
        while True:
            response = await self._graph("GET", path)
            for item in response.get("value", []):
                if "file" not in item or "deleted" in item:
                    continue
                name = str(item.get("name", ""))
                rel_path = _relative_path(item)
                if matches_globs(self.exclude_globs, rel_path):
                    continue
                if self.include_globs and not matches_globs(self.include_globs, rel_path):
                    continue
                if not is_ingestible(name):
                    continue
                item["_relative_path"] = rel_path
                items.append(item)
            next_link = response.get("@odata.nextLink")
            if not next_link:
                break
            path = str(next_link)[len(self.client.base_url) :]
        return items

    async def _download(self, url: str) -> bytes:
        if self._download_client is None:
            self._download_client = httpx.AsyncClient(verify=self._verify)
        response = await self._download_client.get(url)
        response.raise_for_status()
        return response.content


__all__ = ["SharePointSource"]
