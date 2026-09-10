"""Pluggable secret resolution (alvis.secrets)."""

from __future__ import annotations

import httpx
import pytest

from alvis import secrets
from alvis.secrets import (
    EnvSecretResolver,
    VaultSecretResolver,
    configure_from_env,
    resolve_secret,
    resolver,
    set_resolver,
)


@pytest.fixture(autouse=True)
def _restore_default_resolver():
    original = resolver()
    yield
    set_resolver(original)


def test_env_resolver_reads_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALVIS_TEST_SECRET_X", "shh")
    assert EnvSecretResolver().resolve("ALVIS_TEST_SECRET_X") == "shh"


def test_env_resolver_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALVIS_TEST_SECRET_NOPE", raising=False)
    assert EnvSecretResolver().resolve("ALVIS_TEST_SECRET_NOPE") is None


def test_default_resolver_is_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALVIS_TEST_SECRET_Y", "value")
    assert resolve_secret("ALVIS_TEST_SECRET_Y") == "value"


def test_set_resolver_overrides_resolve_secret() -> None:
    class _Fake:
        def resolve(self, ref: str) -> str | None:
            return f"resolved:{ref}"

    set_resolver(_Fake())
    assert resolve_secret("anything") == "resolved:anything"


def test_configure_from_env_defaults_to_env_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALVIS_SECRETS_BACKEND", raising=False)
    set_resolver(EnvSecretResolver())
    configure_from_env()
    assert isinstance(resolver(), EnvSecretResolver)


def test_configure_from_env_switches_to_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALVIS_SECRETS_BACKEND", "vault")
    monkeypatch.setenv("ALVIS_VAULT_ADDR", "http://vault.local")
    monkeypatch.setenv("ALVIS_VAULT_TOKEN", "t0ken")
    configure_from_env()
    assert isinstance(resolver(), VaultSecretResolver)


def test_vault_resolver_reads_kv2_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/secret/data/alvis"
        assert request.headers["x-vault-token"] == "t0ken"
        return httpx.Response(200, json={"data": {"data": {"openai_api_key": "sk-live"}}})

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> httpx.Response:
        request = httpx.Request("GET", url, headers=headers)
        return httpx.Client(transport=httpx.MockTransport(handler)).send(request)

    monkeypatch.setattr(secrets.httpx, "get", fake_get)
    resolver_instance = VaultSecretResolver(addr="http://vault.local", token="t0ken")
    assert resolver_instance.resolve("secret/alvis#openai_api_key") == "sk-live"


def test_vault_resolver_missing_config_returns_none() -> None:
    resolver_instance = VaultSecretResolver(addr=None, token=None)
    assert resolver_instance.resolve("secret/alvis#key") is None


def test_vault_resolver_rejects_malformed_ref() -> None:
    resolver_instance = VaultSecretResolver(addr="http://vault.local", token="t")
    assert resolver_instance.resolve("no-hash-here") is None


def test_vault_resolver_unreachable_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(*args: object, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(secrets.httpx, "get", fake_get)
    resolver_instance = VaultSecretResolver(addr="http://vault.local", token="t")
    assert resolver_instance.resolve("secret/alvis#key") is None
