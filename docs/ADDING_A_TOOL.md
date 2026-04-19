# Adding a New Service Tool

One-paragraph recipe for tool authors.

## Steps

1. **Create the pack YAML** under `agent-tools/src/agent_tools/packs/services/`:

```yaml
# agent-tools/src/agent_tools/packs/services/linear.yaml
id: toolpack://services/linear
version: "1.0.0"
description: "Linear issue tracking — create, search, update issues"
setup_instructions: "Set LINEAR_API_KEY env var"
tools:
  - id: "tool://linear_create_issue"
    adapter_class: http_api
    timeout: 15
    retries: 1
    output_normalization: json
    audit_logging: true
  - id: "tool://linear_search"
    adapter_class: http_api
    timeout: 15
    retries: 0
    output_normalization: json
    audit_logging: true

# Credential declarations — auto-propagated by CredentialRegistry
credentials:
  - name: LINEAR_API_KEY
    purpose: "Linear personal API key — generate at linear.app/settings/api"
    required: true

default_policy:
  sandbox: false
  auth_mode: api_key
  credential_source: "env:LINEAR_API_KEY"   # legacy alias
```

2. **That's it.** From this point:
   - Any agent declaring `tools: [linear_create_issue]` automatically requires `LINEAR_API_KEY`
   - `./ai credentials status` picks it up
   - `./ai test smoke --preflight` validates it
   - `REQUIRED_CREDENTIALS.md` lists it on next genesis run
   - No harness code changes, no Builder prompt changes, no agent manifest changes

## Credential declaration rules

- `name` — the env var name (e.g. `LINEAR_API_KEY`). This IS the lookup key.
- `purpose` — one sentence + "how to get it" link. Shown in error messages and `./ai credentials status`.
- `required: true` — missing value raises a warning at boot and is listed in preflight. Set `false` for optional/feature-flag credentials.

## Testing your new tool

```bash
# Check the credential is picked up
./ai credentials status

# Run preflight (should show your new credential)
./ai test smoke --preflight

# Run registry unit test to verify pack indexing
.venv/bin/pytest harness/tests/test_credential_registry.py -v
```
