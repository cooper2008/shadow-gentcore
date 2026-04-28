# Security Model

This document describes the security architecture of the gentcore framework,
covering permission layers, credential handling, audit logging, and write-back gates.

---

## Permission Layers

Gentcore enforces a 6-layer permission merge at runtime via `RuleEngine`
(`harness/core/rule_engine.py`):

| Layer | Source | Override priority |
|-------|--------|-------------------|
| 1. Platform rules | `config/rules.yaml` `platform_rules:` | Non-negotiable — cannot be overridden |
| 2. Industry rules | `config/rules.yaml` `industry_rules:` | Inherited unless domain overrides |
| 3. Domain rules | `domain.yaml` `rules:` | Domain-level policy |
| 4. Agent manifest | `agent_manifest.yaml` `permissions:` | Per-agent capabilities |
| 5. Task envelope | Runtime `TaskEnvelope.allowed_actions` | Per-run restrictions |
| 6. Tool execution | `ToolExecutor` adapter checks | Enforced at call site |

Rules merge from bottom to top: platform rules are always applied last and override everything.

### Default-On Enforcement

Rule enforcement is **on by default**. To disable (for testing only):

```bash
export GENTCORE_UNSAFE_DISABLE_RULES=1
```

This env var is named `UNSAFE` intentionally. Never set it in production.

---

## Credential Handling

Credentials are resolved through a pluggable backend chain (`harness/core/credential_backends.py`).
Resolution order (first non-None result wins):

1. **EnvBackend** — `os.environ` (default)
2. **FileBackend** — `~/.gentcore/credentials.json` (mode 0600, warns if group/world-readable)
3. **AWSSecretsBackend** — AWS Secrets Manager by prefix or explicit ARN mapping
4. **VaultBackend** — HashiCorp Vault KV v2 by path template

Backend chain is configured in `config/credentials.yaml`. When the file is absent,
defaults to env-only.

### Credential Auto-Propagation

The genesis Builder derives `required_credentials:` for each generated agent from its
tool packs and writes `REQUIRED_CREDENTIALS.md` to the domain root. Run:

```bash
./ai credentials status --domain <path>
```

to see per-agent resolution status before executing any workflow.

---

## Audit Logging

Every tool call, rule decision, and agent execution is logged via Python's standard
`logging` module at the `DEBUG` level with structured fields:

- `agent_id`, `task_id` — trace correlation
- `tool_name`, `action` — what was invoked
- `rule_applied`, `decision` — which rule fired and the outcome
- `duration_ms` — execution time

Configure log level via `GENTCORE_LOG_LEVEL` (default: `WARNING` in production,
`DEBUG` in dry-run). In production, pipe logs to your SIEM.

---

## Write-Back Gates (gentcore-template)

When agents write output back to the domain repository via GitHub Actions:

1. **Secret scan** — gitleaks runs on staged files before commit.
   Any detected secret pattern aborts the workflow with exit code 1.
2. **Dry-run flag** — `dry_run: true` in workflow dispatch skips all write-back steps entirely.
3. **Explicit staged paths** — the commit step only stages known output directories
   (`context/`, `agents/`, `workflows/`, `src/`, `tests/`, `docs/`), not `git add -A`.

---

## Egress Guard

`harness/core/egress_guard.py` intercepts outbound HTTP calls from tool adapters:

- Blocks requests to private IP ranges (RFC 1918, loopback, link-local)
- Blocks requests to cloud metadata endpoints (169.254.169.254, etc.)
- Logs blocked attempts at WARNING level with destination IP

This prevents prompt-injection attacks that attempt to exfiltrate credentials via
SSRF through tool execution.

---

## TarSlip Guard (GitHub Source Adapter)

`harness/core/source_adapters/github.py` enforces safe extraction of source tarballs:

- Rejects absolute paths and `..` traversal in member names
- Rejects symlinks and hardlinks (prevents write-through to sensitive files)
- Rejects device, FIFO, and character special files
- Uses Python 3.12+ `filter="data"` extraction where available

---

## MCP Tool Security

Synthesized tool packs are scanned by `harness/core/tool_security_scanner.py` before
registration. The scanner blocks packs that:

- Declare overly broad shell execution capabilities
- Reference executable paths outside the repo sandbox
- Include credential exfiltration patterns in their descriptions

---

## Known Limitations

- **acme-backend** uses placeholder password hashing (`hashed:{password}`) and has no
  transaction guards on stock decrements. It is marked DEMO ONLY and must not be deployed.
- **`GENTCORE_UNSAFE_DISABLE_RULES=1`** completely bypasses rule enforcement.
  There is no audit trail for this bypass — use only in dev/test environments.
- **Per-step permissions** are not supported by GitHub Actions; `contents: write`
  in agent-task.yml applies to the entire job. The secret scan gate is the primary
  mitigating control.
