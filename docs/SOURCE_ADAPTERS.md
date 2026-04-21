# Source Adapters — Genesis Input Sources

Genesis agents learn from your source material. Source material can live in many places: a local checkout, GitHub, Confluence, Notion, Jira. The **source adapter** layer gives genesis a single API over all of them.

## Why this exists

Before source adapters, the SourceScannerAgent could only read **local filesystem paths**. Teams with sources on GitHub had to clone manually, and docs in Confluence/Notion were completely out of reach.

The adapter layer **materializes** remote sources into a local cache directory. The scanner's existing filesystem logic then works unchanged — `file_read`, `list_dir`, `search_code` operate on the materialized copy. This keeps genesis simple while making remote sources first-class.

## URI schemes (today)

| Scheme | Example | Status |
|---|---|---|
| `file://` / bare path | `/Users/you/repo` or `file:///abs/path` | ✓ built-in |
| `github://` | `github://acme/backend@main?path=src` | ✓ built-in |
| `confluence://` | `confluence://ENG` | 🚧 stub — PR welcome |
| `notion://` | `notion://pages/<pageid>` | 🚧 stub — PR welcome |
| `jira://` | `jira://project/PROJ` | 🚧 stub — PR welcome |

## Usage in `domain.yaml`

Single source (back-compat):

```yaml
reference_source: /Users/you/acme-backend
```

Multiple sources (recommended):

```yaml
reference_sources:
  - uri: github://acme/backend@main
    shard_filter: "src/**/*.py"
    credential: GITHUB_TOKEN
  - uri: confluence://ENG
    shard_filter: "Backend-*"
    credential: CONFLUENCE_API_TOKEN
  - uri: /Users/you/acme-local-docs
    shard_filter: "**/*.md"
```

Fields:
- `uri` (required) — URI following a registered scheme. Plain paths count as `file://`.
- `shard_filter` (optional) — glob applied post-materialization by the scanner to narrow scope.
- `credential` (optional) — explicit credential name override. When unset, the adapter uses its declared `required_credentials`.

## Credentials

Adapters declare what they need. Credentials resolve via **CredentialRegistry** (env → file → AWS Secrets → Vault). Set via environment:

```bash
export GITHUB_TOKEN=ghp_...
export CONFLUENCE_API_TOKEN=...
export CONFLUENCE_EMAIL=you@acme.com
```

Or via `config/credentials.yaml`:

```yaml
GITHUB_TOKEN: ghp_...
CONFLUENCE_API_TOKEN: ...
```

Missing credentials raise a clear error **before** the adapter makes a network call:

```
Missing required credentials for source adapter: ['GITHUB_TOKEN'].
Set via environment variables, `config/credentials.yaml`, or
pass an explicit `credential:` override on the source spec.
```

## GitHub adapter — deep dive

`github://<org>/<repo>[@<ref>][?path=<subdir>]`

- `<ref>` can be a branch name, tag, or commit SHA. Defaults to the repo's default branch (via `HEAD`).
- `?path=subdir` materializes only that subtree. Combined with `shard_filter`, you get precise control.

**How it works:**
1. Resolves `<ref>` to a commit SHA via `GET /repos/{org}/{repo}/commits/{ref}` (so the cache key is deterministic).
2. Downloads the tarball (`GET /repos/.../tarball/{sha}`).
3. Extracts under `~/.cache/gentcore/sources/github/<org>/<repo>/<sha>/`.
4. Writes a `.materialized_sha` marker — subsequent runs with the same ref skip the download.

**Unauthenticated use:** works for public repos but hits GitHub's 60 req/hour limit. The adapter logs a warning. Set `GITHUB_TOKEN` for 5000 req/hour.

**Refresh:** to force re-download (bypass cache), run with `GENTCORE_SOURCE_REFRESH=1`.

**Security:** tar extraction rejects absolute paths and path traversal (`..` segments) to prevent write-outside-cache attacks.

## Cache layout

```
~/.cache/gentcore/sources/
  github/
    acme/
      backend/
        <sha-1>/           ← materialized content
          .materialized_sha
          src/
          README.md
          ...
```

- One directory per `(org, repo, sha)` tuple.
- SHA addressing means pinning a ref means pinning content forever.
- Override cache root with `GENTCORE_CACHE_DIR=/tmp/gentcore`.

## Adding a new adapter

1. Subclass `SourceAdapter` (in `harness/core/source_adapters/base.py`):

```python
from harness.core.source_adapters.base import SourceAdapter, SourceSpec

class ConfluenceAdapter(SourceAdapter):
    scheme = "confluence"
    required_credentials = ["CONFLUENCE_API_TOKEN", "CONFLUENCE_EMAIL", "CONFLUENCE_BASE_URL"]

    async def materialize(self, spec, credentials, cache_dir):
        # Parse spec.uri → SPACE/PARENT_PAGE
        # Paginate CQL search, write each page as .md to cache_dir / "confluence" / space
        # Respect shard_filter (e.g., "Backend-*") in CQL query
        # Return cache root
        ...
```

2. Register it:

```python
# in harness/core/source_adapters/__init__.py
from harness.core.source_adapters.confluence import ConfluenceAdapter
register_adapter(ConfluenceAdapter)
```

3. Add tests modeled on `test_source_adapters.py`.

## Shard filter (scoping large repos)

For monorepos you don't want the scanner to walk 100K files. Use `shard_filter`:

```yaml
reference_sources:
  - uri: github://acme/monorepo@main
    shard_filter: "services/backend/**/*.py"
```

Today the filter is applied by the scanner **after** materialization (via the glob inside SourceScannerAgent's react prompt). Adapters that can push down the filter server-side (GitHub sparse-checkout, Confluence CQL) will do so in a later phase, saving bandwidth.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `FileNotFoundError: GitHub repo or ref not found` | Typo, private repo without token, or deleted branch | Check URI; set `GITHUB_TOKEN` |
| `Missing required credentials` | Adapter declares cred; env + file backends don't resolve it | `export NAME=value` or add to `config/credentials.yaml` |
| `HTTPStatusError 401` | Token invalid/expired/wrong scope | Regenerate token; GitHub needs `repo` scope for private |
| Materialization slow on re-runs | Cache invalidated | Check `~/.cache/gentcore/sources/` exists; set `GENTCORE_CACHE_DIR` to a persistent path (not `/tmp`) |
| Rate limited by GitHub | Unauthenticated burst | Set `GITHUB_TOKEN` |

## Extension points summary

| Want to... | Do this |
|---|---|
| Add new service (e.g. Bitbucket) | Subclass `SourceAdapter`, register in `__init__.py` |
| Override credential resolution globally | Replace `EnvBackend` call in `registry._resolve_credentials` with `ChainedBackend` |
| Add server-side shard pushdown for GitHub | Extend `GitHubAdapter.materialize` to build a sparse-checkout spec when `shard_filter` is set |
| Change cache dir | `export GENTCORE_CACHE_DIR=/my/cache` |
| Force re-download | `export GENTCORE_SOURCE_REFRESH=1` |
