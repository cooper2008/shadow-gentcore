"""SourceAdapter base — common interface for materializing remote → local."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SourceSpec:
    """Declarative spec for a reference source.

    Attributes:
        uri: Source URI. Schemes: `file://<path>`, `github://<org>/<repo>[@ref][?path=sub]`,
            bare filesystem paths are also accepted (treated as `file://`).
        shard_filter: Optional glob to narrow materialization scope
            (e.g. `src/**/*.py`). When supported by the adapter this is
            pushed down to the API (GitHub sparse-checkout, Confluence CQL);
            otherwise applied post-materialization by the scanner.
        credential: Optional explicit credential name override. When unset,
            the adapter uses its `required_credentials` to resolve from
            CredentialRegistry.
        metadata: Adapter-specific extras (ref, branch, spacekey, etc.) —
            usually parsed out of `uri` but can be set explicitly.
    """

    uri: str
    shard_filter: str | None = None
    credential: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(ABC):
    """Base class for source adapters.

    Subclasses materialize the remote source to a local directory. The
    rest of the framework (SourceScannerAgent, file_read, etc.) then sees
    local files and needs zero changes.

    Required class attrs:
        scheme: URI scheme this adapter handles (e.g. "github", "file").
        required_credentials: Credential names this adapter needs to run.
            Empty list for public/local sources. Resolved via CredentialRegistry.

    Conventions:
        * Materialized output MUST be idempotent: running `materialize()`
          twice with the same `SourceSpec` returns the same local path and
          (ideally) skips network calls when cache is fresh.
        * Cache lives under `cache_dir / <adapter-specific subdir>`.
        * Errors (auth, rate-limit, 404) raise exceptions; the loader
          surfaces them with the source URI for debugging.
    """

    scheme: str = ""
    required_credentials: list[str] = []

    @abstractmethod
    async def materialize(
        self,
        spec: SourceSpec,
        credentials: dict[str, str],
        cache_dir: Path,
    ) -> Path:
        """Fetch source content to a local directory.

        Args:
            spec: Source spec (uri + filters + metadata).
            credentials: Resolved credentials keyed by name
                (subset of `required_credentials`).
            cache_dir: Root cache directory. Adapters create subdirs under here.

        Returns:
            Local path to the materialized root (directory for multi-file
            sources, file for single-page sources). Scanner walks from here.
        """
        ...

    def describe(self) -> str:
        """Human-readable description for logs."""
        return f"{self.scheme}: {type(self).__name__}"
