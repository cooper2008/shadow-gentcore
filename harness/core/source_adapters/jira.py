"""Jira adapter — materializes issues from a project as local markdown.

Status: **skeleton**. Framework wiring ready; JQL search + issue-to-markdown
conversion left as a contribution.

URI format:
    jira://<PROJECT_KEY>                       — all issues in a project
    jira://<PROJECT_KEY>?jql=<url-encoded>     — custom JQL filter
    jira://<PROJECT_KEY>?status=Done,InReview  — shorthand status filter

Why genesis wants Jira: incident tickets, bug reports, feature specs are
rich domain knowledge. Feeding them into SourceScanner lets the knowledge
map pick up real terminology and workflows the team uses.

Credentials:
    JIRA_BASE_URL     — e.g. https://acme.atlassian.net
    JIRA_EMAIL        — Atlassian account email
    JIRA_API_TOKEN    — from https://id.atlassian.com/manage-profile/security
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from harness.core.source_adapters.base import SourceAdapter, SourceSpec


def _parse_jira_uri(uri: str) -> dict[str, Any]:
    """Extract {project_key, jql, status_filter} from a jira:// URI."""
    if not uri.startswith("jira://"):
        raise ValueError(f"Not a jira:// URI: {uri!r}")
    parsed = urlparse(uri)
    project_key = parsed.netloc
    if not project_key:
        raise ValueError(
            f"Malformed jira URI: {uri!r}. Expected jira://<PROJECT_KEY>[?jql=...|status=...]"
        )
    jql: str | None = None
    status_filter: list[str] | None = None
    if parsed.query:
        qs = parse_qs(parsed.query)
        if "jql" in qs:
            jql = qs["jql"][0]
        if "status" in qs:
            status_filter = [s.strip() for s in qs["status"][0].split(",") if s.strip()]
    return {
        "project_key": project_key,
        "jql": jql,
        "status_filter": status_filter,
    }


class JiraAdapter(SourceAdapter):
    """Skeleton — JQL search + issue-to-markdown conversion."""

    scheme = "jira"
    required_credentials: list[str] = [
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
    ]

    async def materialize(
        self,
        spec: SourceSpec,
        credentials: dict[str, str],
        cache_dir: Path,
    ) -> Path:
        """Fetch issues → local markdown.

        Implementation recipe:

        1. Parse URI → {project_key, jql, status_filter} via `_parse_jira_uri()`.
        2. Build JQL: prefer explicit `jql`, else synthesise from project_key
           + status_filter, e.g. `project = ENG AND status in (Done, "In Review")`.
        3. Auth: `httpx.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)`.
        4. Paginate `POST {BASE_URL}/rest/api/3/search` with body
               {jql: ..., fields: [summary, description, status, issuetype,
                                   priority, labels, components, comment,
                                   created, updated, resolution],
                maxResults: 100, startAt: 0, expand: ["renderedFields"]}.
           Continue until `total` reached.
        5. For each issue, write `cache_dir / "jira" / <project> / <KEY>.md`:
               ---
               key: ENG-123
               summary: "..."
               type: Bug
               status: Done
               priority: High
               labels: [backend, postgres]
               created: 2025-04-01
               updated: 2025-04-15
               ---
               # {summary}
               {rendered description as markdown — from `renderedFields.description`}
               ## Comments
               - **@user** (2025-04-05): comment body
               ...
           Strip Jira's wiki-format / ADF noise. Keep code blocks.
        6. Write manifest.json with {project_key, issue_count, jql, fetched_at}.
        7. Return `cache_dir / "jira" / <project>`.

        Caching: key by JQL + `updated >= <last_fetched>` delta query. A
        subsequent run only fetches issues updated since last cache write.

        Rate limit: Jira Cloud allows ~100 req/sec. Use
        `asyncio.Semaphore(5)`; backoff on 429 with Retry-After header.
        """
        _ = _parse_jira_uri(spec.uri)  # validates URI shape early
        raise NotImplementedError(
            "JiraAdapter.materialize() is a skeleton. See "
            "`docs/SOURCE_ADAPTERS.md` → 'Adding a new adapter' and the "
            "implementation recipe in this file's docstring. URI parsing, "
            "credentials, and scheme registration are complete; only the "
            "JQL search loop + ADF-to-markdown conversion remain."
        )
