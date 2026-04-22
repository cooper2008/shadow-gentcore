# shadow-gentcore

**SDK for a self-building multi-domain AI agent framework.**

Define a domain in one YAML file. Point it at your codebase. Genesis reads your repo and writes a complete multi-workflow agent service — agents + workflows + a triage dispatcher — that handles features, bugs, refactors, and any other task type from a single `/run` endpoint.

---

## In 30 seconds

```bash
# 1. Use the template — it does the four-repo bootstrap for you
git clone <org>/gentcore-template my-domain
cd my-domain && make bootstrap

# 2. Point genesis at your code, run it
$EDITOR domain.yaml                 # set name + purpose
export ANTHROPIC_API_KEY=...
make genesis                        # writes context/, agents/, workflows/

# 3. Start the service, send any task
make serve
curl -X POST localhost:8765/run -d '{"task": "Add /v1/health endpoint"}'
```

Full guide → **[docs/QUICKSTART.md](docs/QUICKSTART.md)** · **[docs/USER_GUIDE_END_TO_END.md](docs/USER_GUIDE_END_TO_END.md)**

Sources can be local **or remote** — point genesis at `github://acme/backend@main` and it fetches + scans via the source adapter (cached, SHA-addressed). Confluence / Notion / Jira scheme stubs in place for extension. See **[docs/SOURCE_ADAPTERS.md](docs/SOURCE_ADAPTERS.md)**.

---

## What it produces

For every team / domain:

```
my-domain/
├── domain.yaml              ← only file you write
├── context/                 ← coding standards + architecture (genesis-written)
├── agents/                  ← 10-20 specialists, mostly catalog-reused
└── workflows/
    ├── triage.yaml          ← one /run entry — auto-dispatches by task type
    ├── feature_delivery.yaml
    ├── bug_fix.yaml
    ├── refactor.yaml
    └── docs_refresh.yaml    (one per workflow type genesis discovered)
```

Self-healing is built in: gates check semantic conditions (`all_passed == true`, `approved == true`), failures trigger feedback loops back to upstream steps, retries inject prior failure context.

---

## The four repos

```
agent-contracts   ← shadow-gentcore   ← agent-tools   ← your-domain
    types               SDK + CLI         tool packs       (e.g. acme-backend)
```

| Repo | Role |
|------|------|
| [`agent-contracts`](../agent-contracts) | Pydantic types — manifests, contracts, enums |
| **`shadow-gentcore`** | The SDK — engine, CLI, genesis agents, shared stage agents |
| [`agent-tools`](../agent-tools) | Tool packs (FastAPI, GitHub, Slack, Jira, observability) + adapter SDK |
| [`gentcore-template`](../gentcore-template) | Starter — clone to make a new domain |

---

## Provider matrix (verified live)

| Provider | Setup | Notes |
|----------|-------|-------|
| Anthropic native (Opus / Sonnet / Haiku) | `ANTHROPIC_API_KEY` | Best for genesis + complex codegen |
| GLM 5.1 (BigModel, Anthropic-compat) | `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` | Excellent quality, low cost |
| Minimax M2.7 (Anthropic-compat) | same as above | Decent codegen, needs prompt nudge |
| OpenAI native (GPT-5.x) | `OPENAI_API_KEY` | Tool use + JSON mode |
| Gemini Flash (OpenAI-compat) | `OPENAI_BASE_URL` + key | Tool-loop translation handled automatically |
| AWS Bedrock | profile / bearer token | Claude on Bedrock |
| Claude Code subscription (`claude -p`) | local CLI | Prose-only — no tool calls |
| `DryRunProvider` | `--dry-run` | Zero-cost pipeline smoke |
| `SmokeTestProvider` | `./ai test smoke` | Schema-correct stubs for CI |

Full setup → **[docs/PROVIDER_GUIDE.md](docs/PROVIDER_GUIDE.md)**.

---

## Core commands

```bash
./ai workspace                                       # show registered domains + tool packs
./ai genesis scan   --team my-team --dry-run
./ai genesis build  --team my-team [--dry-run]
./ai run agent      <id> --task "..." --domain PATH [--dry-run]
./ai run workflow   PATH --task '{...}' [--dry-run]
./ai serve          --domain PATH --port 8765       # HTTP server
./ai test smoke     [--preflight|--cross-domain|--domain PATH|--verbose]
```

---

## Runtime features

- **Multi-workflow per domain** — auto-discovered from git log + issue templates + CONTRIBUTING.md, dispatched via a router gate on `triage.yaml`
- **Self-healing DAGs** — gates + retry-with-feedback + feedback_loops + reset_points + rollback + router/approval gates
- **5 execution modes** — `react`, `plan_execute`, `chain_of_thought`, `self_ask`, `tree_of_thought`
- **Tiered memory** — Tier 1 `standards.md` always-injected → Tier 2 `context_retrieve` (keyword-indexed chunks) → Tier 3 `origin_fetch` (live re-fetch via SourceAdapter cache, scope-guarded, audit-logged) → Tier 4 `memory_recall` (persistent JSONL store). No embeddings, no vector DB. See **[docs/MEMORY_GUIDE.md](docs/MEMORY_GUIDE.md)**.
- **Catalog-first design** — Architect v2 picks from 23 reusable `_shared/` stage agents (TestRunner, Reviewer, Linter, Migration, SecurityScan, Compliance, RetrievalAgent, TriageAgent, ObservabilityAgent, …) before synthesising new ones
- **Auto tool-gap closing** — `ToolSynthesizerAgent` closes gaps by wrapping existing MCP servers or synthesizing new tool packs, gated by a static security scanner
- **6-layer permission engine** — platform rules non-negotiable, hot-reloadable from `config/rules.yaml`
- **Credential auto-propagation** — Builder stamps `required_credentials:` on each agent from declared tools; writes `REQUIRED_CREDENTIALS.md`; OAuth groups handled as first-class setup units
- **Prompt ↔ manifest contract validator** — CI-grade static check that prompt promises match manifest reality (`./ai validate-contracts --domain <path>`)
- **Per-model prompt nudges** — opt-in for weaker models (MiniMax, Gemini Flash, GLM); strong models get no bloat. Gemini 3 `thought_signature` auto-preserved.
- **Schema-enforced output** — every agent declares `output_schema`; the validator + gate evaluator read fields directly
- **Emit-then-write Builder** — single-turn LLM call emits `files: [{path, content}]`, Python hook writes. Works on MiniMax / GLM without multi-turn `file_write` hang.

---

## Key paths

| Path | What |
|------|------|
| `harness/core/` | Engine — AgentRunner, CompositionEngine, RuleEngine, OutputValidator, **WorkflowResolver**, MemoryStore |
| `harness/core/expr.py` | Unified gate-condition evaluator (success/completed synonym) |
| `harness/core/modes/` | 5 execution strategies (react / plan_execute / chain_of_thought / self_ask / tree_of_thought) |
| `harness/core/context_retriever.py` | Tier 2 — keyword-indexed chunk retrieval |
| `harness/core/memory_store.py` | Tier 4 — FileMemoryStore + InMemoryMemoryStore |
| `harness/core/source_adapters/` | URI-scheme dispatch (`github://`, `file://`, Confluence/Notion/Jira skeletons) |
| `harness/core/credential_registry.py` | Credential declarations + resolution + OAuth groups |
| `harness/core/tool_security_scanner.py` | Static scanner for synthesized tool packs |
| `harness/core/prompt_contract_validator.py` | Prompt ↔ manifest drift check |
| `harness/providers/` | Anthropic, OpenAI, Bedrock, ClaudeCode, DryRun, SmokeTest, **model_hints** (+Gemini 3 thought_signature round-trip) |
| `harness/server/app.py` | FastAPI agent service — `/run`, `/run/agent`, `/run/workflow`, `/agents`, approval endpoints |
| `harness/cli/ai.py` | `./ai` CLI |
| `harness/tools/builtin.py` | Built-in tool adapters incl. `context_retrieve`, `origin_fetch`, `memory_recall` |
| `agents/_genesis/` | 9 genesis agents — Scanner, Mapper, ToolDiscovery, ContextEngineer, ToolSynthesizer, Architect (v1+v2), Builder, QualityGate, Evolution |
| `agents/_shared/` | 23 reusable stage agents |
| `config/` | `rules.yaml`, `workspace.yaml`, `capabilities.yaml`, `industries.yaml`, `known_mcp_servers.yaml`, `tool_security.yaml` |
| `scripts/` | Runnable examples — smoke + real-LLM harnesses for 5+ providers |

---

## Docs index

| Doc | When to read |
|-----|--------------|
| [QUICKSTART.md](docs/QUICKSTART.md) | First time — 5 minutes to a running agent |
| [USER_GUIDE_END_TO_END.md](docs/USER_GUIDE_END_TO_END.md) | Full user journey + override examples |
| [MEMORY_GUIDE.md](docs/MEMORY_GUIDE.md) | Tier 1–5 memory architecture (standards → chunks → origin_fetch → memory_recall → evolution) |
| [SOURCE_ADAPTERS.md](docs/SOURCE_ADAPTERS.md) | Reading from GitHub / Confluence / Notion / Jira as genesis input |
| [CREDENTIALS_GUIDE.md](docs/CREDENTIALS_GUIDE.md) | API keys + OAuth2 groups, auto-propagation to agents |
| [PROVIDER_GUIDE.md](docs/PROVIDER_GUIDE.md) | Wiring an LLM (native or compat gateway, incl. Gemini 3.1 Pro) |
| [AUTOMATION_GUIDE.md](docs/AUTOMATION_GUIDE.md) | CI / cron / webhook patterns |
| [SYSTEM_GUIDE.md](docs/SYSTEM_GUIDE.md) | Deep architecture |
| [TEAM_GUIDE.md](docs/TEAM_GUIDE.md) | Operational patterns for domain teams |
| [FACTORY_VS_BUILDER.md](docs/FACTORY_VS_BUILDER.md) | On-demand single-agent vs full domain genesis |
| [FRAMEWORK_AUDIT_2026Q2.md](docs/FRAMEWORK_AUDIT_2026Q2.md) | Audit findings + Phase 2 fix log |

---

## Tests

```bash
.venv/bin/pytest harness/tests/ -q          # 1748 tests
./ai test smoke                             # full journey, zero tokens
./ai test smoke --cross-domain              # backend + frontend in parallel
./ai validate-contracts --domain <path>     # CI gate: prompt ↔ manifest drift check
```
