"""Pluggable credential-resolution backends.

Every backend implements the tiny `CredentialBackend` protocol — a single
`resolve(name) -> str | None` call. The built-ins cover the common cases:

  * EnvBackend          — os.environ.get() (default)
  * FileBackend         — JSON file on disk (e.g. ~/.gentcore/credentials.json)
  * AWSSecretsBackend   — AWS Secrets Manager, keyed by prefix/mapping
  * VaultBackend        — HashiCorp Vault KV v2
  * ChainedBackend      — first-match wins across an ordered list

Backend choice is wired by `CredentialRegistry` from `config/credentials.yaml`
or defaults to env-only when the file is absent.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable


logger = logging.getLogger(__name__)


# ── Protocol ──────────────────────────────────────────────────────────────


@runtime_checkable
class CredentialBackend(Protocol):
    """Resolve a credential by canonical name (e.g. 'JIRA_API_TOKEN').

    Return None when the backend has no value for this name — the registry
    then falls through to the next backend (or raises).
    """

    def resolve(self, name: str) -> str | None: ...


# ── Env ───────────────────────────────────────────────────────────────────


class EnvBackend:
    """Reads credentials from `os.environ`. The default.

    The canonical name IS the env-var name. No mapping, no transformation.
    """

    def resolve(self, name: str) -> str | None:
        value = os.environ.get(name)
        return value if value else None

    def __repr__(self) -> str:
        return "EnvBackend()"


# ── File ──────────────────────────────────────────────────────────────────


@dataclass
class FileBackend:
    """Reads credentials from a JSON file (dict of name → value).

    Good for local dev when you don't want 10 env exports. The file should
    be mode 0600 (the backend does not enforce this, but a warning is
    logged if it's group- or world-readable).
    """

    path: Path | str = "~/.gentcore/credentials.json"
    _cache: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _loaded: bool = field(default=False, init=False, repr=False)

    def resolve(self, name: str) -> str | None:
        self._ensure_loaded()
        value = self._cache.get(name)
        return value if value else None

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        expanded = Path(self.path).expanduser()
        if not expanded.exists():
            self._loaded = True
            return
        try:
            if expanded.stat().st_mode & 0o077:
                logger.warning(
                    "FileBackend: %s is group/world-readable (mode=%o); "
                    "consider `chmod 600`",
                    expanded, expanded.stat().st_mode & 0o777,
                )
            data = json.loads(expanded.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._cache = {str(k): str(v) for k, v in data.items() if v}
            else:
                logger.error("FileBackend: %s must be a JSON object", expanded)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("FileBackend: could not read %s: %s", expanded, exc)
        self._loaded = True


# ── AWS Secrets Manager ───────────────────────────────────────────────────


@dataclass
class AWSSecretsBackend:
    """Reads credentials from AWS Secrets Manager.

    Two lookup modes:
      * `mapping={CREDNAME: "arn:aws:..."}` — explicit per-credential ARN
      * `prefix="gentcore/prod/"` — compose ARN as `<prefix><credname>`

    Mapping wins when both are set. Either can be empty.

    Values are fetched lazily and cached for the lifetime of the backend.
    Fails closed (returns None) when boto3 isn't installed OR when the
    secret isn't found; this lets the chained backend fall back to env
    during local dev without a crash.
    """

    region: str = "us-east-1"
    prefix: str = ""
    mapping: dict[str, str] = field(default_factory=dict)
    _cache: dict[str, str | None] = field(default_factory=dict, init=False, repr=False)
    _client: Any = field(default=None, init=False, repr=False)

    def resolve(self, name: str) -> str | None:
        if name in self._cache:
            return self._cache[name]
        arn_or_id = self.mapping.get(name) or (self.prefix + name if self.prefix else None)
        if not arn_or_id:
            self._cache[name] = None
            return None
        client = self._get_client()
        if client is None:
            self._cache[name] = None
            return None
        try:
            resp = client.get_secret_value(SecretId=arn_or_id)
            value = resp.get("SecretString")
        except Exception as exc:  # boto errors vary — treat all as miss
            logger.debug("AWSSecretsBackend miss for %s: %s", name, exc)
            value = None
        self._cache[name] = value if value else None
        return self._cache[name]

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
            self._client = boto3.client("secretsmanager", region_name=self.region)
        except ImportError:
            logger.warning(
                "AWSSecretsBackend: boto3 not installed; falling through. "
                "Install with `pip install boto3`."
            )
            self._client = None
        return self._client


# ── HashiCorp Vault (KV v2) ───────────────────────────────────────────────


@dataclass
class VaultBackend:
    """Reads credentials from Vault KV v2 engine.

    `path_template` is a format string using `{name}` for the credential
    name. Example: `secret/data/gentcore/{name}` reads
    `secret/data/gentcore/JIRA_API_TOKEN` when asked for `JIRA_API_TOKEN`.

    Requires `VAULT_ADDR` and `VAULT_TOKEN` env vars (or `hvac.Client`
    kwargs via constructor). Fails closed (returns None) when hvac isn't
    installed or the path is missing.
    """

    path_template: str = "secret/data/gentcore/{name}"
    field_name: str = "value"  # inside the KV v2 "data" dict, which key holds the secret
    _cache: dict[str, str | None] = field(default_factory=dict, init=False, repr=False)
    _client: Any = field(default=None, init=False, repr=False)

    def resolve(self, name: str) -> str | None:
        if name in self._cache:
            return self._cache[name]
        client = self._get_client()
        if client is None:
            self._cache[name] = None
            return None
        path = self.path_template.format(name=name)
        try:
            response = client.read(path)
        except Exception as exc:
            logger.debug("VaultBackend miss for %s at %s: %s", name, path, exc)
            response = None
        value: str | None = None
        if response and isinstance(response, dict):
            # KV v2 wraps actual data under data.data
            data = response.get("data", {}).get("data", {}) or response.get("data", {})
            raw = data.get(self.field_name) if isinstance(data, dict) else None
            value = str(raw) if raw else None
        self._cache[name] = value
        return value

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import hvac
            self._client = hvac.Client(
                url=os.environ.get("VAULT_ADDR", ""),
                token=os.environ.get("VAULT_TOKEN", ""),
            )
            if not self._client.is_authenticated():
                logger.warning("VaultBackend: client not authenticated")
                self._client = None
        except ImportError:
            logger.warning(
                "VaultBackend: hvac not installed; falling through. "
                "Install with `pip install hvac`."
            )
            self._client = None
        return self._client


# ── Chained ───────────────────────────────────────────────────────────────


@dataclass
class ChainedBackend:
    """Composes multiple backends — first non-None result wins.

    Typical production wiring: `ChainedBackend([EnvBackend(),
    AWSSecretsBackend(prefix="gentcore/prod/")])` so local env overrides
    always win (handy for debugging) but prod pulls from Secrets Manager
    when env is unset.
    """

    backends: list[CredentialBackend]

    def resolve(self, name: str) -> str | None:
        for backend in self.backends:
            value = backend.resolve(name)
            if value:
                return value
        return None


# ── Factory (config-driven wiring) ────────────────────────────────────────


def backend_from_config(config: dict[str, Any] | None) -> CredentialBackend:
    """Build a backend from a `config/credentials.yaml` block.

    None or empty config returns EnvBackend (the default for local dev).
    A config with `backends: [...]` returns a ChainedBackend in order.
    """
    if not config:
        return EnvBackend()
    entries: Iterable[dict[str, Any]] = config.get("backends") or []
    if not entries:
        return EnvBackend()
    resolved: list[CredentialBackend] = []
    for entry in entries:
        btype = entry.get("type", "env")
        if btype == "env":
            resolved.append(EnvBackend())
        elif btype == "file":
            resolved.append(FileBackend(path=entry.get("path", "~/.gentcore/credentials.json")))
        elif btype == "aws_secrets":
            resolved.append(AWSSecretsBackend(
                region=entry.get("region", "us-east-1"),
                prefix=entry.get("prefix", ""),
                mapping=entry.get("mapping") or {},
            ))
        elif btype == "vault":
            resolved.append(VaultBackend(
                path_template=entry.get("path_template", "secret/data/gentcore/{name}"),
                field_name=entry.get("field", "value"),
            ))
        else:
            logger.warning("Unknown credential backend type %r; skipping", btype)
    if not resolved:
        return EnvBackend()
    if len(resolved) == 1:
        return resolved[0]
    return ChainedBackend(resolved)
