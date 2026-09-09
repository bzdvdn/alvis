"""S3 source — SigV4-signed ListObjectsV2 + GetObject (no boto3)."""

from __future__ import annotations

import hashlib
import hmac
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from alvis.core.models import Artifact, DocumentMeta
from alvis.sources.base import SourceError
from alvis.sources.content_types import content_type, is_ingestible, matches_globs
from alvis.sources.http import HttpClient

_S3_XML = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


class SigV4Signer:
    """AWS Signature Version 4 for the ``s3`` service."""

    def __init__(self, access_key: str, secret_key: str, region: str) -> None:
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region

    def headers(
        self,
        method: str,
        target_path: str,
        query: dict[str, Any] | None,
        host: str,
    ) -> dict[str, str]:
        """Return the AWS SigV4 signing headers for a request."""
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = amz_date[:8]

        canonical_uri = quote(target_path, safe="/") or "/"
        canonical_query = _canonical_query(query)
        payload_hash = _EMPTY_SHA256  # GET requests carry no body

        signed_headers, canonical_headers = _canonical_headers(
            host, amz_date, payload_hash
        )
        canonical_request = "\n".join(
            [
                method.upper(),
                canonical_uri,
                canonical_query,
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        scope = f"{datestamp}/{self.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                _sha256(canonical_request.encode()),
            ]
        )
        signing_key = _hmac(
            _hmac(
                _hmac(_hmac(f"AWS4{self.secret_key}".encode(), datestamp), self.region),
                "s3",
            ),
            "aws4_request",
        )
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        credential = f"{self.access_key}/{scope}"
        return {
            "Host": host,
            "X-Amz-Date": amz_date,
            "X-Amz-Content-Sha256": payload_hash,
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={credential}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
        }


def _canonical_query(query: dict[str, Any] | None) -> str:
    if not query:
        return ""
    parts = []
    for key in sorted(query):
        value = str(query[key])
        parts.append(f"{quote(key, safe='')}={quote(value, safe='')}")
    return "&".join(parts)


def _canonical_headers(host: str, amz_date: str, payload_hash: str) -> tuple[str, str]:
    raw = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    names = sorted(raw)
    canonical = "".join(f"{name}:{raw[name].strip()}\n" for name in names)
    return ";".join(names), canonical


class S3Source:
    """Yields an artifact per text object in an S3 bucket.

    Config keys: ``url`` (endpoint, e.g. ``http://localhost:9000``),
    ``bucket``, ``access_key_env``, ``secret_key_env``, ``region``
    (default "us-east-1"), ``prefix`` (optional key prefix served
    server-side — restricts listing to a directory), ``include_globs``
    (only matching keys are ingested), ``exclude_globs`` (matching keys
    are skipped, e.g. videos — wins over ``include_globs``). Objects
    are addressed path-style (works with MinIO and other S3-compatible
    servers). Requests are signed with SigV4 (no boto3 dependency).
    """

    def __init__(
        self,
        url: str,
        bucket: str,
        *,
        access_key_env: str,
        secret_key_env: str,
        region: str = "us-east-1",
        prefix: str | None = None,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix
        self.include_globs = include_globs
        self.exclude_globs = exclude_globs
        access_key = os.environ.get(access_key_env)
        secret_key = os.environ.get(secret_key_env)
        if not access_key or not secret_key:
            raise SourceError(
                f"environment variables {access_key_env!r} and {secret_key_env!r} "
                f"must be set to use the s3 source"
            )
        signer = SigV4Signer(access_key, secret_key, region)
        self.host = url.split("://", 1)[-1].rstrip("/")
        self.client = HttpClient(
            base_url=url,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
            transport=transport,
            header_hook=lambda method, path, query: signer.headers(
                method, path, query, self.host
            ),
            max_bytes=max_bytes,
        )

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self.client.aclose()

    async def list_documents(self) -> list[DocumentMeta]:
        """Fingerprint objects from the listing (ETag, no body download)."""
        metas: list[DocumentMeta] = []
        for key, etag, size in await self._list_objects():
            if not self._wanted(key):
                continue
            metas.append(
                DocumentMeta(
                    uri=_object_uri(self.client.base_url, self.bucket, key),
                    step_id=_sha256(f"{self.bucket}/{key}".encode()),
                    fingerprint=etag or f"size:{size}",
                    content_type=content_type(key),
                )
            )
        return metas

    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]:
        """List the bucket and fetch the wanted text objects as artifacts.

        With ``uris``, only the given object URIs are downloaded.
        """
        artifacts: list[Artifact] = []
        for key, _, _ in await self._list_objects():
            if not self._wanted(key):
                continue
            uri = _object_uri(self.client.base_url, self.bucket, key)
            if uris is not None and uri not in uris:
                continue
            try:
                blob = await self.client.request(
                    "GET",
                    f"/{self.bucket}/{quote(key, safe='/')}",
                    ok_status=(200,),
                    raw=True,
                )
            except SourceError as exc:
                if exc.status_code == 413:
                    continue  # oversized object skipped, others abort the run
                raise
            artifacts.append(
                Artifact(
                    step_id=_sha256(f"{self.bucket}/{key}".encode()),
                    uri=uri,
                    content_type=content_type(key),
                    data=blob,
                    metadata={
                        "path": key,
                        "title": key.rsplit("/", 1)[-1],
                        "bucket": self.bucket,
                    },
                )
            )
        return artifacts

    async def _list_objects(self) -> list[tuple[str, str, str]]:
        objects: list[tuple[str, str, str]] = []
        token: str | None = None
        while True:
            query: dict[str, str] = {"list-type": "2"}
            if self.prefix:
                query["prefix"] = self.prefix
            if token:
                query["continuation-token"] = token
            resp = await self.client.request(
                "GET",
                f"/{self.bucket}",
                query=query,
                ok_status=(200,),
                raw=True,
            )
            root = ET.fromstring(resp)
            for child in root.iter(f"{_S3_XML}Contents"):
                key = child.findtext(f"{_S3_XML}Key") or ""
                etag = child.findtext(f"{_S3_XML}ETag") or ""
                size = child.findtext(f"{_S3_XML}Size") or "0"
                objects.append((key, etag.strip('"'), size))
            if root.findtext(f"{_S3_XML}IsTruncated") != "true":
                break
            token = root.findtext(f"{_S3_XML}NextContinuationToken")
        return objects

    def _wanted(self, key: str) -> bool:
        if matches_globs(self.exclude_globs, key):
            return False
        if self.include_globs is not None:
            return matches_globs(self.include_globs, key)
        return is_ingestible(key)


def _object_uri(base_url: str, bucket: str, key: str) -> str:
    return f"{base_url}/{bucket}/{key}"