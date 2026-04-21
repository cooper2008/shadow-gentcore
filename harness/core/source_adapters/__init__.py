"""Source adapter framework for genesis agents.

Problem: SourceScannerAgent today only reads local filesystem paths. Real
teams' sources live on GitHub, Confluence, Notion, Jira — each with its own
auth, rate limits, and shape. Without a common abstraction, the scanner
would need N parallel codepaths.

Solution: a pluggable `SourceAdapter` that "materializes" remote data to a
local cache directory. The scanner keeps its existing filesystem logic —
everything upstream just becomes files on disk.

Extension points:
  * Add a new service: subclass `SourceAdapter`, declare `scheme`,
    `required_credentials`, and implement `materialize()`. Register via
    `register_adapter()`.
  * URI schemes today: `file://`, `github://`, plus bare paths.
  * Back-compat: bare local paths (no scheme) dispatch to LocalFSAdapter.
"""

from __future__ import annotations

from harness.core.source_adapters.base import SourceAdapter, SourceSpec
from harness.core.source_adapters.confluence import ConfluenceAdapter
from harness.core.source_adapters.github import GitHubAdapter
from harness.core.source_adapters.jira import JiraAdapter
from harness.core.source_adapters.local_fs import LocalFSAdapter
from harness.core.source_adapters.notion import NotionAdapter
from harness.core.source_adapters.registry import register_adapter, resolve_source

# Auto-register built-in adapters on import.
# Skeleton adapters (Confluence, Notion, Jira) register their scheme so
# URIs parse cleanly and credentials validate, but raise NotImplementedError
# on materialize() with a pointer to the implementation recipe. Framework
# users get clear errors instead of "unknown scheme".
register_adapter(LocalFSAdapter)
register_adapter(GitHubAdapter)
register_adapter(ConfluenceAdapter)
register_adapter(NotionAdapter)
register_adapter(JiraAdapter)

__all__ = [
    "SourceAdapter",
    "SourceSpec",
    "register_adapter",
    "resolve_source",
    "LocalFSAdapter",
    "GitHubAdapter",
    "ConfluenceAdapter",
    "NotionAdapter",
    "JiraAdapter",
]
