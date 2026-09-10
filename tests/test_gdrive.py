"""GoogleDriveSource — RS256 JWT signing + REST client against a mocked Drive API."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from alvis.sources import gdrive as gdrive_module
from alvis.sources.base import SourceError
from alvis.sources.gdrive import GoogleDriveSource, _sign_service_account_jwt


@pytest.fixture(scope="module")
def rsa_keypair():  # noqa: ANN201
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return private_key, private_pem


@pytest.fixture()
def service_account_key(rsa_keypair) -> str:  # noqa: ANN001
    _, private_pem = rsa_keypair
    return json.dumps(
        {"client_email": "svc@project.iam.gserviceaccount.com", "private_key": private_pem}
    )


def _source(*, token_handler=None, drive_handler=None) -> GoogleDriveSource:  # noqa: ANN001
    source = GoogleDriveSource(service_account_key_env="GDRIVE_KEY")
    if token_handler is not None:
        source._token_client.transport = httpx.MockTransport(token_handler)
    source.client.transport = httpx.MockTransport(drive_handler)
    return source


async def _token_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})


def _file(file_id: str, name: str, mime_type: str = "text/plain", **extra) -> dict:  # noqa: ANN003
    return {
        "id": file_id,
        "name": name,
        "mimeType": mime_type,
        "modifiedTime": "2026-01-01T00:00:00Z",
        "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
        **extra,
    }


def _drive_router(*, files: list[dict], content: dict[str, bytes] | None = None):  # noqa: ANN201
    content = content or {}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok-1"
        path = request.url.path
        if path == "/drive/v3/files":
            return httpx.Response(200, json={"files": files})
        if path.endswith("/export"):
            file_id = path.split("/")[-2]
            return httpx.Response(200, content=content[file_id])
        if "/files/" in path:
            file_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, content=content[file_id])
        raise AssertionError(f"unexpected request to {path}")

    return handler


def test_sign_service_account_jwt_produces_a_verifiable_signature(rsa_keypair) -> None:  # noqa: ANN001
    private_key, private_pem = rsa_keypair
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    token = _sign_service_account_jwt("svc@project.iam.gserviceaccount.com", private_pem)
    header_b64, claims_b64, sig_b64 = token.split(".")

    def _pad(s: str) -> bytes:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

    header = json.loads(_pad(header_b64))
    claims = json.loads(_pad(claims_b64))
    assert header == {"alg": "RS256", "typ": "JWT"}
    assert claims["iss"] == "svc@project.iam.gserviceaccount.com"
    assert claims["scope"] == "https://www.googleapis.com/auth/drive.readonly"
    assert claims["exp"] > claims["iat"]

    signing_input = f"{header_b64}.{claims_b64}".encode("ascii")
    public_key = private_key.public_key()
    public_key.verify(_pad(sig_b64), signing_input, padding.PKCS1v15(), hashes.SHA256())


def test_sign_service_account_jwt_requires_cryptography(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gdrive_module, "_CRYPTOGRAPHY", False)
    with pytest.raises(SourceError, match="cryptography"):
        _sign_service_account_jwt("svc@x", "not-a-real-key")


async def test_missing_service_account_secret_raises() -> None:
    source = GoogleDriveSource(service_account_key_env="MISSING_GDRIVE_KEY_XYZ")
    with pytest.raises(SourceError, match="MISSING_GDRIVE_KEY_XYZ"):
        await source.list_documents()


async def test_malformed_service_account_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GDRIVE_KEY", "not json at all")
    source = GoogleDriveSource(service_account_key_env="GDRIVE_KEY")
    with pytest.raises(SourceError, match="not a valid Google service-account"):
        await source.list_documents()


async def test_list_documents_fingerprints_by_modified_time(
    monkeypatch: pytest.MonkeyPatch, service_account_key: str
) -> None:
    monkeypatch.setenv("GDRIVE_KEY", service_account_key)
    files = [_file("1", "readme.md"), _file("2", "notes.txt")]
    source = _source(token_handler=_token_ok, drive_handler=_drive_router(files=files))

    metas = await source.list_documents()

    assert [m.step_id for m in metas] == ["1", "2"]
    assert metas[0].fingerprint == "2026-01-01T00:00:00Z"
    assert metas[0].content_type == "text/markdown"
    assert metas[0].uri == "https://drive.google.com/file/d/1/view"


async def test_list_documents_skips_folders_and_unsupported_google_apps_types(
    monkeypatch: pytest.MonkeyPatch, service_account_key: str
) -> None:
    monkeypatch.setenv("GDRIVE_KEY", service_account_key)
    files = [
        _file("f1", "Reports", mime_type="application/vnd.google-apps.folder"),
        _file("s1", "Budget", mime_type="application/vnd.google-apps.spreadsheet"),
        _file("d1", "Plan", mime_type="application/vnd.google-apps.document"),
        _file("2", "video.mp4", mime_type="video/mp4"),
        _file("3", "readme.md"),
    ]
    source = _source(token_handler=_token_ok, drive_handler=_drive_router(files=files))

    metas = await source.list_documents()

    assert [m.step_id for m in metas] == ["d1", "3"]


async def test_list_documents_respects_include_globs(
    monkeypatch: pytest.MonkeyPatch, service_account_key: str
) -> None:
    monkeypatch.setenv("GDRIVE_KEY", service_account_key)
    files = [_file("1", "a.md"), _file("2", "b.txt")]
    source = GoogleDriveSource(
        service_account_key_env="GDRIVE_KEY", include_globs=["*.md"]
    )
    source._token_client.transport = httpx.MockTransport(_token_ok)
    source.client.transport = httpx.MockTransport(_drive_router(files=files))

    metas = await source.list_documents()

    assert [m.step_id for m in metas] == ["1"]


async def test_list_documents_paginates(
    monkeypatch: pytest.MonkeyPatch, service_account_key: str
) -> None:
    monkeypatch.setenv("GDRIVE_KEY", service_account_key)
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok-1"
        if "pageToken" not in request.url.params:
            calls.append(1)
            return httpx.Response(
                200, json={"files": [_file("1", "a.md")], "nextPageToken": "p2"}
            )
        calls.append(2)
        return httpx.Response(200, json={"files": [_file("2", "b.md")]})

    source = _source(token_handler=_token_ok, drive_handler=handler)

    metas = await source.list_documents()

    assert calls == [1, 2]
    assert [m.step_id for m in metas] == ["1", "2"]


async def test_fetch_downloads_regular_file_via_alt_media(
    monkeypatch: pytest.MonkeyPatch, service_account_key: str
) -> None:
    monkeypatch.setenv("GDRIVE_KEY", service_account_key)
    files = [_file("1", "readme.md")]
    source = _source(
        token_handler=_token_ok,
        drive_handler=_drive_router(files=files, content={"1": b"# Hello"}),
    )

    artifacts = await source.fetch()

    assert len(artifacts) == 1
    assert artifacts[0].data == b"# Hello"
    assert artifacts[0].content_type == "text/markdown"
    assert artifacts[0].metadata["documentId"] == "1"


async def test_fetch_exports_google_doc_as_plain_text(
    monkeypatch: pytest.MonkeyPatch, service_account_key: str
) -> None:
    monkeypatch.setenv("GDRIVE_KEY", service_account_key)
    files = [_file("d1", "Plan", mime_type="application/vnd.google-apps.document")]
    source = _source(
        token_handler=_token_ok,
        drive_handler=_drive_router(files=files, content={"d1": b"Plan contents"}),
    )

    artifacts = await source.fetch()

    assert artifacts[0].data == b"Plan contents"
    assert artifacts[0].content_type == "text/plain"


async def test_fetch_enforces_max_bytes(
    monkeypatch: pytest.MonkeyPatch, service_account_key: str
) -> None:
    monkeypatch.setenv("GDRIVE_KEY", service_account_key)
    files = [_file("1", "big.md")]
    source = GoogleDriveSource(service_account_key_env="GDRIVE_KEY", max_bytes=4)
    source._token_client.transport = httpx.MockTransport(_token_ok)
    source.client.transport = httpx.MockTransport(
        _drive_router(files=files, content={"1": b"way too many bytes"})
    )

    assert await source.fetch() == []


async def test_fetch_filters_by_uris(
    monkeypatch: pytest.MonkeyPatch, service_account_key: str
) -> None:
    monkeypatch.setenv("GDRIVE_KEY", service_account_key)
    files = [_file("1", "a.md"), _file("2", "b.md")]
    source = _source(
        token_handler=_token_ok,
        drive_handler=_drive_router(files=files, content={"1": b"a", "2": b"b"}),
    )

    artifacts = await source.fetch(
        uris={"https://drive.google.com/file/d/2/view"}
    )

    assert [a.uri for a in artifacts] == ["https://drive.google.com/file/d/2/view"]


async def test_token_is_cached_across_calls(
    monkeypatch: pytest.MonkeyPatch, service_account_key: str
) -> None:
    monkeypatch.setenv("GDRIVE_KEY", service_account_key)
    token_calls = {"n": 0}

    async def token_handler(request: httpx.Request) -> httpx.Response:
        token_calls["n"] += 1
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})

    files = [_file("1", "a.md")]
    source = _source(token_handler=token_handler, drive_handler=_drive_router(files=files))

    await source.list_documents()
    await source.list_documents()

    assert token_calls["n"] == 1


def test_file_uri_falls_back_when_no_web_view_link() -> None:
    assert (
        GoogleDriveSource._file_uri({"id": "abc"})
        == "https://drive.google.com/file/d/abc/view"
    )


def test_rejects_max_concurrency_below_one() -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        GoogleDriveSource(service_account_key_env="GDRIVE_KEY", max_concurrency=0)


async def test_aclose_releases_clients(
    monkeypatch: pytest.MonkeyPatch, service_account_key: str
) -> None:
    monkeypatch.setenv("GDRIVE_KEY", service_account_key)
    files = [_file("1", "a.md")]
    source = _source(token_handler=_token_ok, drive_handler=_drive_router(files=files))

    await source.list_documents()
    await source.aclose()
