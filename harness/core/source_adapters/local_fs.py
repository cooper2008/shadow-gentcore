"""Local filesystem adapter — no-op materialize (source IS already local)."""

from __future__ import annotations

from pathlib import Path

from harness.core.source_adapters.base import SourceAdapter, SourceSpec


class LocalFSAdapter(SourceAdapter):
    """Materialize local filesystem paths. No network, no credentials.

    Accepts `file:///abs/path` or bare paths (`/abs/path`, `~/rel`).
    Validates existence; leaves the tree in place.
    """

    scheme = "file"
    required_credentials: list[str] = []

    async def materialize(
        self,
        spec: SourceSpec,
        credentials: dict[str, str],
        cache_dir: Path,
    ) -> Path:
        uri = spec.uri
        # Strip scheme prefix if present
        if uri.startswith("file://"):
            uri = uri[len("file://"):]
        path = Path(uri).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(
                f"Local source does not exist: {path} (from uri={spec.uri!r})"
            )
        return path
