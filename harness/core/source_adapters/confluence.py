"""Confluence adapter — materializes a Confluence space to local markdown.

Status: **skeleton**. The framework is wired (scheme registered, credentials
declared, URI parser works) but `materialize()` raises NotImplementedError
with a precise implementation recipe. See docs/SOURCE_ADAPTERS.md for the
"how to add a new adapter" guide.

URI format:
    confluence://<SPACEKEY>                       — whole space
    confluence://<SPACEKEY>?parent=<PAGEID>       — subtree under a page
    confluence://<SPACEKEY>?cql=<encoded-CQL>     — CQL-filtered subset

Credentials (all required):
    CONFLUENCE_BASE_URL   — e.g. https://acme.atlassian.net
    CONFLUENCE_EMAIL      — Atlassian account email
    CONFLUENCE_API_TOKEN  — from https://id.atlassian.com/manage-profile/security
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from harness.core.source_adapters.base import SourceAdapter, SourceSpec


def _parse_confluence_uri(uri: str) -> dict[str, Any]:
    """Extract {space_key, parent_id, cql} from a confluence:// URI."""
    if not uri.startswith("confluence://"):
        raise ValueError(f"Not a confluence:// URI: {uri!r}")
    parsed = urlparse(uri)
    space_key = parsed.netloc
    if not space_key:
        raise ValueError(
            f"Malformed confluence URI: {uri!r}. "
            "Expected confluence://<SPACEKEY>[?parent=PAGEID|cql=...]"
        )
    parent_id: str | None = None
    cql: str | None = None
    if parsed.query:
        qs = parse_qs(parsed.query)
        if "parent" in qs:
            parent_id = qs["parent"][0]
        if "cql" in qs:
            cql = qs["cql"][0]
    return {"space_key": space_key, "parent_id": parent_id, "cql": cql}


class ConfluenceAdapter(SourceAdapter):
    """Skeleton — fill in with REST pagination + HTML→markdown conversion."""

    scheme = "confluence"
    required_credentials: list[str] = [
        "CONFLUENCE_BASE_URL",
        "CONFLUENCE_EMAIL",
        "CONFLUENCE_API_TOKEN",
    ]

    async def materialize(
        self,
        spec: SourceSpec,
        credentials: dict[str, str],
        cache_dir: Path,
    ) -> Path:
        """Fetch all pages in the space → local markdown files.

        Implementation recipe (for the contributor who finishes this):

        1. Parse URI → space_key, parent_id, cql via `_parse_confluence_uri()`.
        2. Build auth: `httpx.BasicAuth(CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN)`.
        3. Paginate `GET {BASE_URL}/wiki/rest/api/content` with params:
               spaceKey=<space>, expand=body.storage,version, limit=100
           Use `_links.next` for pagination. If `parent_id` given, filter
           `ancestors` to include parent. If `cql` given, use `/search?cql=...`.
        4. For each page, write to `cache_dir / "confluence" / <space> / <page-slug>.md`:
               ---
               title: <page.title>
               version: <page.version.number>
               id: <page.id>
               updated: <page.version.when>
               ---
               <body.storage.value converted to markdown>
           HTML→markdown: use `markdownify` (add to pyproject.toml) or
           `html2text`. Preserve code blocks with language hints from
           `<ac:structured-macro name="code">` tags.
        5. Write manifest.json with {space_key, page_count, latest_version}
           alongside for cache invalidation.
        6. Return `cache_dir / "confluence" / <space>`.

        Caching: key the cache on (space_key, parent_id, cql, max_version).
        Re-runs compare `max_version` via a lightweight `/content?limit=1&orderby=-version`
        call; skip re-materialization if unchanged.

        Rate limiting: Confluence Cloud allows ~50 req/sec. Use
        `asyncio.Semaphore(5)` to stay well under.
        """
        _ = _parse_confluence_uri(spec.uri)  # validates URI shape early
        raise NotImplementedError(
            "ConfluenceAdapter.materialize() is a skeleton. See "
            "`docs/SOURCE_ADAPTERS.md` → 'Adding a new adapter' and the "
            "implementation recipe in this file's docstring. The framework "
            "(URI parsing, credential resolution, scheme registration) is "
            "ready — only the HTTP + markdown conversion is missing."
        )
