"""Google Drive source — fetches files a service account can see, via the Drive v3 API.

Authenticates with a Google service account: a self-signed RS256 JWT
(``urn:ietf:params:oauth:grant-type:jwt-bearer``) exchanged for an OAuth2
access token — Google's server-to-server flow, no user/browser in the
loop. Unlike every other adapter in this package, that JWT needs real RSA
signing, which the standard library has no primitive for — this is the one
connector in Alvis with a non-optional-at-runtime third-party dependency
(``cryptography``, ``pip install alvis[gdrive]``), rather than the
"hand-roll the HTTP calls" approach used everywhere else (S3's SigV4 is
HMAC, not RSA — plain ``hmac``/``hashlib`` sufficed there).
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import time
from typing import Any

from alvis._concurrency import gather_bounded
from alvis.core.models import Artifact, DocumentMeta
from alvis.secrets import resolve_secret
from alvis.sources.base import SourceError
from alvis.sources.content_types import content_type, is_ingestible, matches_globs
from alvis.sources.http import HttpClient

_TOKEN_URL_BASE = "https://oauth2.googleapis.com"
_DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_TOKEN_EXPIRY_SECONDS = 3600
_TOKEN_EXPIRY_SAFETY_MARGIN = 60.0
_PAGE_SIZE = 100
_FIELDS = "nextPageToken, files(id,name,mimeType,modifiedTime,webViewLink,parents)"

_CRYPTOGRAPHY = importlib.util.find_spec("cryptography") is not None
_GDRIVE_EXTRA = "install the optional extra: pip install alvis[gdrive]"

#: Google-native document types this connector can export to plain text.
#: Sheets/Slides/Forms/Drawings (and anything else under
#: application/vnd.google-apps.*) have no meaningful plain-text export and
#: are skipped rather than silently mis-rendered.
_EXPORT_MIME_TYPES: dict[str, str] = {
    "application/vnd.google-apps.document": "text/plain",
}
_GOOGLE_APPS_PREFIX = "application/vnd.google-apps."


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign_service_account_jwt(client_email: str, private_key_pem: str) -> str:
    """Build and RS256-sign a Google service-account JWT assertion."""
    if not _CRYPTOGRAPHY:
        raise SourceError(f"google drive source requires cryptography ({_GDRIVE_EXTRA})")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": client_email,
        "scope": _SCOPE,
        "aud": f"{_TOKEN_URL_BASE}/token",
        "iat": now,
        "exp": now + _TOKEN_EXPIRY_SECONDS,
    }
    signing_input = (
        f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url(json.dumps(claims, separators=(',', ':')).encode())}"
    )
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"), password=None
    )
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise SourceError(
            f"google drive service-account private key must be RSA, got "
            f"{type(private_key).__name__}"
        )
    signature = private_key.sign(
        signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
    )
    return f"{signing_input}.{_b64url(signature)}"


class GoogleDriveSource:
    """Yields one artifact per ingestible file the service account can see.

    Config keys: ``service_account_key_env`` (env var holding the *entire*
    service-account JSON key, as downloaded from Google Cloud Console —
    ``client_email`` + ``private_key``), ``folder_id`` (optional — scope to
    one Drive folder's direct children; subfolders are **not** traversed
    recursively in this version, only files directly in the given folder,
    or every file the service account has been shared when omitted),
    ``include_globs``/``exclude_globs`` (matched against the file name),
    ``max_bytes``, ``max_concurrency``.

    Google-native documents (Docs/Sheets/Slides/Forms/Drawings) have no
    fixed byte content; only Google Docs are exported (as plain text via
    Drive's ``/export`` endpoint) — everything else under
    ``application/vnd.google-apps.*`` is skipped, not mis-rendered.
    """

    def __init__(
        self,
        service_account_key_env: str,
        folder_id: str | None = None,
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
        self.service_account_key_env = service_account_key_env
        self.folder_id = folder_id
        self.include_globs = include_globs
        self.exclude_globs = exclude_globs
        self.max_bytes = max_bytes
        self._token_client = HttpClient(
            base_url=_TOKEN_URL_BASE, retries=retries, retry_backoff=retry_backoff, verify=verify
        )
        self.client = HttpClient(
            base_url=_DRIVE_API_BASE,
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

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pools."""
        await self._token_client.aclose()
        await self.client.aclose()

    async def list_documents(self) -> list[DocumentMeta]:
        """Fingerprint files by their last-modified timestamp (metadata only)."""
        files = await self._list_files()
        return [
            DocumentMeta(
                uri=self._file_uri(item),
                step_id=str(item["id"]),
                fingerprint=str(item.get("modifiedTime", "")),
                content_type=self._content_type(item),
            )
            for item in files
        ]

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        """Download the wanted files (or exports, for Google Docs) as artifacts.

        With ``uris``, only the given files are downloaded. Downloads run
        concurrently, bounded by ``max_concurrency``.
        """
        files = await self._list_files()
        wanted = [item for item in files if uris is None or self._file_uri(item) in uris]

        async def _fetch_one(item: dict[str, Any]) -> Artifact | None:
            mime_type = str(item.get("mimeType", ""))
            file_id = str(item["id"])
            if mime_type.startswith(_GOOGLE_APPS_PREFIX):
                export_mime = _EXPORT_MIME_TYPES[mime_type]
                data = await self._graph_bytes(
                    f"/files/{file_id}/export", query={"mimeType": export_mime}
                )
            else:
                data = await self._graph_bytes(f"/files/{file_id}", query={"alt": "media"})
            if self.max_bytes is not None and len(data) > self.max_bytes:
                return None
            return Artifact(
                step_id=file_id,
                uri=self._file_uri(item),
                content_type=self._content_type(item),
                data=data,
                metadata={
                    "path": str(item.get("name", "")),
                    "title": str(item.get("name", "")),
                    "documentId": file_id,
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
            raw_key = resolve_secret(self.service_account_key_env)
            if not raw_key:
                raise SourceError(
                    f"secret {self.service_account_key_env!r} is not set "
                    f"(configured via service_account_key_env)"
                )
            try:
                key_data = json.loads(raw_key)
                client_email = str(key_data["client_email"])
                private_key_pem = str(key_data["private_key"])
            except (json.JSONDecodeError, KeyError) as exc:
                raise SourceError(
                    f"secret {self.service_account_key_env!r} is not a valid Google "
                    f"service-account JSON key: {exc}"
                ) from exc
            assertion = _sign_service_account_jwt(client_email, private_key_pem)
            response = await self._token_client.request(
                "POST",
                "/token",
                form={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
            self._access_token = str(response["access_token"])
            expires_in = float(response.get("expires_in", _TOKEN_EXPIRY_SECONDS))
            self._token_expiry = time.monotonic() + expires_in - _TOKEN_EXPIRY_SAFETY_MARGIN

    async def _graph(self, path: str, *, query: dict[str, Any] | None = None) -> dict[str, Any]:
        await self._ensure_token()
        return await self.client.request("GET", path, query=query)

    async def _graph_bytes(self, path: str, *, query: dict[str, Any] | None = None) -> bytes:
        await self._ensure_token()
        return await self.client.request("GET", path, query=query, raw=True)

    async def _list_files(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        query_parts = ["trashed = false"]
        if self.folder_id:
            query_parts.append(f"'{self.folder_id}' in parents")
        while True:
            params: dict[str, Any] = {
                "q": " and ".join(query_parts),
                "fields": _FIELDS,
                "pageSize": _PAGE_SIZE,
            }
            if page_token:
                params["pageToken"] = page_token
            response = await self._graph("/files", query=params)
            for item in response.get("files", []):
                if not self._wanted(item):
                    continue
                items.append(item)
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return items

    def _wanted(self, item: dict[str, Any]) -> bool:
        name = str(item.get("name", ""))
        mime_type = str(item.get("mimeType", ""))
        if mime_type == "application/vnd.google-apps.folder":
            return False
        if mime_type.startswith(_GOOGLE_APPS_PREFIX) and mime_type not in _EXPORT_MIME_TYPES:
            return False
        if not mime_type.startswith(_GOOGLE_APPS_PREFIX) and not is_ingestible(name):
            return False
        if matches_globs(self.exclude_globs, name):
            return False
        if self.include_globs:
            return matches_globs(self.include_globs, name)
        return True

    @staticmethod
    def _content_type(item: dict[str, Any]) -> str:
        mime_type = str(item.get("mimeType", ""))
        if mime_type in _EXPORT_MIME_TYPES:
            return _EXPORT_MIME_TYPES[mime_type]
        return content_type(str(item.get("name", "")))

    @staticmethod
    def _file_uri(item: dict[str, Any]) -> str:
        web_view_link = item.get("webViewLink")
        if web_view_link:
            return str(web_view_link)
        return f"https://drive.google.com/file/d/{item['id']}/view"


__all__ = ["GoogleDriveSource"]
