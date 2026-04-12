# Tool Integration Guide

How to create tool adapters and tool packs for the `agent-tools` library.

## Overview

Tools are referenced by URI in agent manifests:
- `tool://pytest` — a single tool
- `toolpack://python_build` — a pack of tools

Tool manifests live in `agent_tools/packs/*.yaml`. Adapters live in `agent_tools/adapters/`.

## Tool Manifest Fields

```yaml
id: "tool://my_tool"
adapter_class: cli          # cli | mcp | http_api
timeout: 60                 # seconds
retries: 1
rate_limit: null            # max calls/min, null = unlimited
sandbox: false
auth_mode: none             # none | env_var | api_key | oauth | secret_manager
credential_source: null     # e.g. "MY_TOOL_API_KEY"
output_normalization: text  # text | json | artifact
audit_logging: true
```

## Adapter Classes

### CLIToolAdapter

Wraps shell commands. Output captured as stdout/stderr.

```python
from agent_tools.adapters.cli_adapter import CLIToolAdapter
from agent_contracts.manifests.tool_manifest import ToolManifest, AdapterClass

manifest = ToolManifest(id="tool://pytest", adapter_class=AdapterClass.CLI, timeout=120)
adapter = CLIToolAdapter(manifest)
result = await adapter.invoke("pytest", ["tests/", "-v"])
# result: { exit_code, stdout, stderr, success, tool_id }
```

### MCPToolAdapter

Connects to an MCP server and invokes tools by name.

```python
from agent_tools.adapters.mcp_adapter import MCPToolAdapter

manifest = ToolManifest(id="tool://mcp_search", adapter_class=AdapterClass.MCP)
adapter = MCPToolAdapter(manifest)
await adapter.connect("ws://localhost:9999")
result = await adapter.invoke("search", {"query": "python error handling"})
await adapter.disconnect()
```

### HTTPAPIToolAdapter

Invokes HTTP endpoints with auth, retry, and rate limiting.

```python
from agent_tools.adapters.http_api_adapter import HTTPAPIToolAdapter

manifest = ToolManifest(
    id="tool://github_api",
    adapter_class=AdapterClass.HTTP_API,
    auth_mode=AuthMode.API_KEY,
    credential_source="GITHUB_TOKEN",
    retries=3,
    rate_limit=60,
)
adapter = HTTPAPIToolAdapter(manifest)
result = await adapter.invoke("POST", "https://api.github.com/repos/org/repo/issues",
    headers={"Authorization": "token ..."},
    body={"title": "Bug report"},
)
```

## Normalizing Output

All adapters provide `normalize_output()` → `ArtifactRecord`:

```python
artifact = adapter.normalize_output(result, artifact_id="run-123")
# ArtifactRecord(artifact_id="run-123", type=ArtifactType.TOOL_OUTPUT, ...)
```

## Creating a Tool Pack

Tool packs bundle related tools with shared defaults.

```yaml
# agent_tools/packs/my_pack.yaml
id: "toolpack://my_pack"
version: "1.0.0"
tools:
  - "tool://my_tool_a"
  - "tool://my_tool_b"
default_policy:
  sandbox: false
  auth_mode: none
```

Reference in a domain manifest:
```yaml
default_tool_packs:
  - "toolpack://my_pack"
```

## Resolving Tools

```python
from agent_tools.resolver import ToolResolver

resolver = ToolResolver(pack_dirs=["agent_tools/packs"])
pack = resolver.resolve_pack("toolpack://python_build")
missing = resolver.validate_availability(["tool://pytest", "toolpack://unknown"])
```

## Built-In Packs

| Pack URI | Tools |
|----------|-------|
| `toolpack://python_build` | pytest, mypy, ruff, pip |
| `toolpack://java_build` | maven, gradle, checkstyle |
| `toolpack://go_build` | go test, golangci-lint, go build |
| `toolpack://build_core` | git, make, docker |
| `toolpack://github_pr` | gh CLI for PR management |
| `toolpack://browser` | Playwright browser automation |
| `toolpack://observability` | metrics, tracing, log shipping |
