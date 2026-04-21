"""Adapter registry + resolve_source entry point.

Usage in genesis workflow:
    from harness.core.source_adapters import resolve_source, SourceSpec
    local_path = await resolve_source(
        SourceSpec(uri="github://acme/backend@main", shard_filter="src/**"),
    )
    # scanner walks local_path as usual
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from harness.core.source_adapters.base import SourceAdapter, SourceSpec

logger = logging.getLogger(__name__)

_ADAPTERS: dict[str, type[SourceAdapter]] = {}


def register_adapter(adapter_cls: type[SourceAdapter]) -> None:
    """Register an adapter class under its `scheme`.

    Built-in adapters are auto-registered via `source_adapters/__init__.py`.
    External code can register custom adapters (e.g. a private backend)
    by calling this before `resolve_source()`.
    """
    scheme = getattr(adapter_cls, "scheme", "")
    if not scheme:
        raise ValueError(f"{adapter_cls.__name__} is missing a `scheme` attribute")
    if scheme in _ADAPTERS and _ADAPTERS[scheme] is not adapter_cls:
        logger.warning("Overriding existing adapter for scheme %r", scheme)
    _ADAPTERS[scheme] = adapter_cls


def get_adapter_class(scheme: str) -> type[SourceAdapter] | None:
    """Return the registered adapter class for a scheme, or None."""
    return _ADAPTERS.get(scheme)


def list_schemes() -> list[str]:
    """List all registered schemes (for CLI help / debugging)."""
    return sorted(_ADAPTERS.keys())


def _default_cache_dir() -> Path:
    """Where adapters stash materialized sources.

    Honors `GENTCORE_CACHE_DIR` env var; defaults to `~/.cache/gentcore/sources`.
    """
    raw = os.environ.get("GENTCORE_CACHE_DIR")
    if raw:
        base = Path(raw).expanduser()
    else:
        base = Path.home() / ".cache" / "gentcore"
    p = base / "sources"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _parse_scheme(uri: str) -> str:
    """Return the URI scheme, or `file` for bare local paths."""
    # Bare absolute path like /abs/path or ~/path
    if uri.startswith("/") or uri.startswith("~") or uri.startswith("."):
        return "file"
    parsed = urlparse(uri)
    return parsed.scheme or "file"


def _resolve_credentials(
    required: list[str],
    explicit: str | None = None,
) -> dict[str, str]:
    """Resolve credentials via CredentialRegistry.

    Prefers explicit override when supplied. Missing non-empty creds raise
    a clear error so the adapter fails fast rather than sending an
    unauthenticated request that returns 401 with a vague message.
    """
    from harness.core.credential_backends import EnvBackend

    # Simple env-first resolver — CredentialRegistry in full mode uses
    # ChainedBackend; for now the scanner runs with env-only which is the
    # lowest-friction default. Larger deployments can wire their own.
    backend = EnvBackend()

    resolved: dict[str, str] = {}
    missing: list[str] = []
    for name in required:
        # Honor explicit override for the first credential if adapter
        # documents a single-credential pattern.
        lookup_name = explicit if (explicit and len(required) == 1) else name
        value = backend.resolve(lookup_name) or ""
        if value:
            resolved[name] = value
        else:
            missing.append(lookup_name)

    if missing:
        raise RuntimeError(
            f"Missing required credentials for source adapter: {missing}. "
            "Set via environment variables, `config/credentials.yaml`, or "
            "pass an explicit `credential:` override on the source spec."
        )
    return resolved


async def resolve_source(
    spec: SourceSpec | str | dict[str, Any],
    cache_dir: Path | None = None,
) -> Path:
    """Materialize a source to a local directory and return the local path.

    Accepts:
      * SourceSpec instance
      * Bare string URI (wraps in SourceSpec)
      * Dict (used when loading from YAML)
    """
    if isinstance(spec, str):
        spec = SourceSpec(uri=spec)
    elif isinstance(spec, dict):
        spec = SourceSpec(
            uri=spec["uri"],
            shard_filter=spec.get("shard_filter"),
            credential=spec.get("credential"),
            metadata={k: v for k, v in spec.items() if k not in ("uri", "shard_filter", "credential")},
        )

    scheme = _parse_scheme(spec.uri)
    cls = _ADAPTERS.get(scheme)
    if cls is None:
        available = ", ".join(list_schemes()) or "(none)"
        raise ValueError(
            f"No source adapter registered for scheme {scheme!r} "
            f"(uri={spec.uri!r}). Available: {available}."
        )

    adapter = cls()
    creds = _resolve_credentials(adapter.required_credentials, spec.credential)
    cache = cache_dir or _default_cache_dir()

    logger.info("Materializing source: uri=%s scheme=%s adapter=%s",
                spec.uri, scheme, cls.__name__)
    local_path = await adapter.materialize(spec, creds, cache)
    logger.info("Materialized: %s → %s", spec.uri, local_path)
    return local_path
