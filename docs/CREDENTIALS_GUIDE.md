# Credentials Guide

Central credential management for service tools in shadow-gentcore.

## How it works

Every tool pack declares the credentials it needs in its YAML manifest. The `CredentialRegistry` reads these declarations at boot and auto-propagates requirements to every agent that declares the tool. No hand-authoring per agent. No silent 401 failures.

```
TOOL PACK YAML        (source of truth — declare once)
 jira.yaml: credentials: [JIRA_EMAIL, JIRA_API_TOKEN, JIRA_BASE_URL]
        │
        ▼  CredentialRegistry indexes at boot
AGENT MANIFEST        (declares tools: [jira_search, ...])
 required_credentials derived — NOT hand-authored
        │
        ▼  ManifestLoader.resolve_required_credentials() at load time
AGENT RUNTIME         (fails fast with actionable error if missing)
```

## Declaring credentials on a tool pack

Add a `credentials:` block to the service pack YAML:

```yaml
# agent-tools/src/agent_tools/packs/services/myservice.yaml
id: toolpack://services/myservice
credentials:
  - name: MYSERVICE_API_KEY
    purpose: "API key — generate at myservice.example.com/settings/api"
    required: true
```

From that point on, any agent declaring `myservice_*` tools automatically requires `MYSERVICE_API_KEY`. No code changes, no Builder prompt changes.

## Configuring credentials

Choose one of three methods (in precedence order with ChainedBackend):

### 1. Environment variables (default, local dev)

```bash
export JIRA_BASE_URL=https://acme.atlassian.net
export JIRA_EMAIL=me@acme.com
export JIRA_API_TOKEN=ATATT3x...
export GITHUB_TOKEN=ghp_...
export SLACK_BOT_TOKEN=xoxb-...
```

### 2. Credentials file (`~/.gentcore/credentials.json`)

```json
{
  "JIRA_API_TOKEN": "ATATT3x...",
  "JIRA_EMAIL": "me@acme.com",
  "JIRA_BASE_URL": "https://acme.atlassian.net",
  "GITHUB_TOKEN": "ghp_...",
  "SLACK_BOT_TOKEN": "xoxb-..."
}
```

### 3. Multi-backend config (`config/credentials.yaml`)

```yaml
backends:
  - type: env                      # always checked first
  - type: file
    path: ~/.gentcore/credentials.json
  - type: aws_secrets
    region: us-east-1
    prefix: gentcore/prod/
  - type: vault
    url: https://vault.example.com
    token_env: VAULT_TOKEN
    mount: secret
    path_prefix: gentcore/
```

Available backends: `env`, `file`, `aws_secrets`, `vault`. `ChainedBackend` tries each in order, first non-empty value wins.

## Checking credential status

```bash
# Show resolution status for all service tool credentials
./ai credentials status

# Show what a specific agent needs
./ai credentials status --agent acme-backend/IncidentTriageAgent/v1

# Show required credentials for one agent (verbose)
./ai credentials required acme-backend/IncidentTriageAgent/v1

# CI gate — exits non-zero if any required credential is unresolved
./ai credentials missing && echo "ok" || echo "has missing"
```

## Preflight check

`./ai test smoke --preflight` includes a `credentials_available` check that reads all service pack declarations and validates resolution. Missing credentials are reported as warnings (not fatal) so local dev without all services still works.

## Fail-fast at runtime

When an agent runs without a required credential, `ManifestLoader.boot_engine()` logs a warning before execution. To make it a hard failure, call `registry.validate(tool_names, raise_on_missing=True)` — this raises `MissingCredentialError` with a clear, actionable message listing each missing credential and its `purpose:` hint.

## Service credential reference

| Credential | Tool pack | Purpose |
|------------|-----------|---------|
| `JIRA_BASE_URL` | `toolpack://services/jira` | `https://acme.atlassian.net` |
| `JIRA_EMAIL` | `toolpack://services/jira` | Atlassian account email |
| `JIRA_API_TOKEN` | `toolpack://services/jira` | [Generate here](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `CONFLUENCE_BASE_URL` | `toolpack://services/confluence` | `https://acme.atlassian.net/wiki` |
| `CONFLUENCE_EMAIL` | `toolpack://services/confluence` | Atlassian account email |
| `CONFLUENCE_API_TOKEN` | `toolpack://services/confluence` | Same token as Jira |
| `GITHUB_TOKEN` | `toolpack://services/github` | [PAT](https://github.com/settings/tokens) — scopes: repo, issues, pull_requests |
| `GITHUB_TOKEN` | `toolpack://services/github_workflow` | Needs workflow scope |
| `SLACK_BOT_TOKEN` | `toolpack://services/slack` | `xoxb-...` from [api.slack.com/apps](https://api.slack.com/apps) |

## Adding a new service tool

See [ADDING_A_TOOL.md](ADDING_A_TOOL.md) for the one-paragraph recipe.

## LLM provider credentials

LLM API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.) are managed separately by `harness/core/runtime.py`. See [PROVIDER_GUIDE.md](PROVIDER_GUIDE.md).
