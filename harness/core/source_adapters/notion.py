"""Notion adapter — materializes a Notion database / page tree to local markdown.

Status: **skeleton**. Framework wiring ready; HTTP + block-to-markdown
conversion left as a contribution.

URI format:
    notion://databases/<database_id>         — all pages in a database
    notion://pages/<page_id>                 — single page + descendants
    notion://workspace                       — every accessible page (expensive!)

Credentials:
    NOTION_API_KEY  — integration token from https://www.notion.so/my-integrations
                      (the database/page must be shared with the integration)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from harness.core.source_adapters.base import SourceAdapter, SourceSpec


def _parse_notion_uri(uri: str) -> dict[str, Any]:
    """Extract {kind, id} where kind is 'databases' | 'pages' | 'workspace'."""
    if not uri.startswith("notion://"):
        raise ValueError(f"Not a notion:// URI: {uri!r}")
    parsed = urlparse(uri)
    kind = parsed.netloc
    target_id = parsed.path.lstrip("/")
    if kind not in ("databases", "pages", "workspace"):
        raise ValueError(
            f"Unknown Notion URI kind: {kind!r}. "
            "Expected notion://databases/<id>, notion://pages/<id>, or notion://workspace"
        )
    if kind != "workspace" and not target_id:
        raise ValueError(f"Notion URI missing id: {uri!r}")
    return {"kind": kind, "id": target_id}


class NotionAdapter(SourceAdapter):
    """Skeleton — Notion API v1 paginated fetch + block→markdown conversion."""

    scheme = "notion"
    required_credentials: list[str] = ["NOTION_API_KEY"]

    async def materialize(
        self,
        spec: SourceSpec,
        credentials: dict[str, str],
        cache_dir: Path,
    ) -> Path:
        """Fetch pages → local markdown.

        Implementation recipe:

        1. Parse URI → {kind, id} via `_parse_notion_uri()`.
        2. Auth header: `Authorization: Bearer {NOTION_API_KEY}` +
           `Notion-Version: 2022-06-28`.
        3. For databases: `POST /v1/databases/{id}/query` (paginated via
           `next_cursor`). Returns page objects; for each, call
           `GET /v1/blocks/{page_id}/children?page_size=100` and recurse
           for nested blocks.
        4. For pages: `GET /v1/pages/{id}` + walk block children.
        5. Convert Notion blocks → markdown. Minimum support:
             paragraph / heading_1-3 / bulleted_list_item / numbered_list_item
             / code / quote / callout / image / table / to_do / divider
           A small block-to-markdown utility is ~200 LOC. Alternative: use
           `notion2md` or `notion-to-md` (npm pkg; Python port exists).
        6. Write each page as `cache_dir / "notion" / <page-slug>.md` with
           frontmatter (id, last_edited_time, parent).
        7. Return `cache_dir / "notion"`.

        Caching: key by `last_edited_time` from page metadata. Single
        `HEAD`-style check on each page before re-fetching.

        Rate limit: Notion's is 3 req/sec averaged. Use
        `asyncio.Semaphore(3)` + retry on 429.
        """
        _ = _parse_notion_uri(spec.uri)  # validates URI shape early
        raise NotImplementedError(
            "NotionAdapter.materialize() is a skeleton. See "
            "`docs/SOURCE_ADAPTERS.md` → 'Adding a new adapter' and the "
            "implementation recipe in this file's docstring. The URI parser, "
            "credential declaration, and scheme registration are complete; "
            "only the HTTP fetch loop + block-to-markdown conversion remain."
        )
