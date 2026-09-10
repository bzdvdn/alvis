"""Pluggable secret resolution — env vars by default, swappable to Vault.

Every adapter that needs a credential (``api_token_env``, ``dsn_env``,
``access_key_env``, ``secret_key_env``, ...) already takes a *reference*
string rather than the secret itself; the string named an environment
variable. This module is that indirection made swappable: adapters call
:func:`resolve_secret` instead of ``os.environ`` directly, and the same
reference string is resolved through whichever :class:`SecretResolver` is
currently configured.

Nothing changes unless you opt in: the default resolver
(:class:`EnvSecretResolver`) reads ``os.environ`` exactly as before. To
source credentials from HashiCorp Vault's KV v2 engine instead — no
``hvac`` dependency, plain HTTP like :mod:`alvis.sources.s3`'s hand-rolled
SigV4 — either construct :class:`VaultSecretResolver` yourself and call
:func:`set_resolver`, or set ``ALVIS_SECRETS_BACKEND=vault`` (plus
``ALVIS_VAULT_ADDR``/``ALVIS_VAULT_TOKEN``) and call
:func:`configure_from_env` once at process startup (the CLI does this).
"""

from __future__ import annotations

import os
from typing import Protocol

import httpx


class SecretResolver(Protocol):
    """Resolves a reference string to a secret value."""

    def resolve(self, ref: str) -> str | None:
        """Return the secret named by ``ref``, or ``None`` if not found."""
        ...


class EnvSecretResolver:
    """Reads secrets from process environment variables (the default)."""

    def resolve(self, ref: str) -> str | None:
        return os.environ.get(ref)


class VaultSecretResolver:
    """Reads secrets from HashiCorp Vault's KV v2 HTTP API.

    ``ref`` is ``<mount>/<path>#<key>`` (e.g. ``secret/alvis#openai_api_key``
    for a secret written at ``secret/alvis`` with a ``openai_api_key``
    field). One request per :meth:`resolve` call — credentials are read
    once at adapter construction time, not per outgoing request, so this
    does not add a Vault round trip to the hot path.

    ``addr``/``token`` default to the ``ALVIS_VAULT_ADDR``/
    ``ALVIS_VAULT_TOKEN`` environment variables when not given explicitly.
    Any failure (Vault unreachable, secret missing, malformed ``ref``)
    returns ``None`` rather than raising — the caller (an adapter's
    ``__init__``) already raises its own clear "not configured" error when
    resolution comes back empty, so this stays a single failure mode for
    callers to handle instead of two.
    """

    def __init__(
        self,
        addr: str | None = None,
        token: str | None = None,
        *,
        timeout: float = 10.0,
    ) -> None:
        self.addr = (addr or os.environ.get("ALVIS_VAULT_ADDR", "")).rstrip("/")
        self.token = token or os.environ.get("ALVIS_VAULT_TOKEN")
        self.timeout = timeout

    def resolve(self, ref: str) -> str | None:
        if not self.addr or not self.token or "#" not in ref:
            return None
        path, key = ref.rsplit("#", 1)
        mount, _, secret_path = path.partition("/")
        if not mount or not secret_path:
            return None
        url = f"{self.addr}/v1/{mount}/data/{secret_path}"
        try:
            response = httpx.get(
                url, headers={"X-Vault-Token": self.token}, timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()["data"]["data"]
        except (httpx.HTTPError, KeyError, ValueError):
            return None
        value = data.get(key)
        return str(value) if value is not None else None


_resolver: SecretResolver = EnvSecretResolver()


def set_resolver(new_resolver: SecretResolver) -> None:
    """Override the process-wide secret resolver (default: env vars)."""
    global _resolver
    _resolver = new_resolver


def resolver() -> SecretResolver:
    """The currently configured resolver."""
    return _resolver


def resolve_secret(ref: str) -> str | None:
    """Resolve ``ref`` (an env var name, or backend-specific reference)
    through the current resolver."""
    return _resolver.resolve(ref)


def configure_from_env() -> None:
    """Switch to :class:`VaultSecretResolver` if ``ALVIS_SECRETS_BACKEND=vault``.

    Called once at CLI/process startup; a no-op (env resolver stays active)
    when the variable is unset or any other value.
    """
    if os.environ.get("ALVIS_SECRETS_BACKEND", "env").strip().lower() == "vault":
        set_resolver(VaultSecretResolver())


__all__ = [
    "EnvSecretResolver",
    "SecretResolver",
    "VaultSecretResolver",
    "configure_from_env",
    "resolve_secret",
    "resolver",
    "set_resolver",
]
