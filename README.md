# shadow-gentcore

**The SDK for a self-building multi-domain AI agent framework.**

Define a domain with one YAML file. Point it at your existing codebase. Genesis reads your repo, writes purpose-built agents, and ships them. Run those agents on real tasks — they write real code with real tool calls, gated by real tests, inside a self-healing DAG.

---

## The four-repo architecture

```
agent-contracts   ← shadow-gentcore   ← agent-tools   ← your-domain (e.g. acme-backend)
    (types)           (SDK + CLI)        (tool packs)       (product repo + domain.yaml)
```

| Repo | Role |
|------|------|
| [`agent-contracts`](../agent-contracts) | Shared Pydantic types: `DomainManifest`, `AgentManifest`, `WorkflowDefinition` |
| **`shadow-gentcore`** | The SDK itself — execution engine, CLI, genesis agents, shared stage agents |
| [`agent-tools`](../agent-tools) | Tool packs (FastAPI, React, GitHub, Slack, etc.) + adapter SDK |
| [`gentcore-template`](../gentcore-template) | Starter template — clone this to make a new domain repo |

Plus your own: [`acme-backend`](../acme-backend) is the worked example — a FastAPI product repo wired as a gentcore domain.

---

## Five-minute quickstart

```bash
# 0. Clone the framework repos side-by-side
git clone <org>/agent-contracts ../agent-contracts
git clone <org>/agent-tools      ../agent-tools
git clone <org>/shadow-gentcore  .
git clone <org>/gentcore-template ../my-backend   # your new domain

# 1. Install
python3.11 -m venv .venv
.venv/bin/pip install -e ../agent-contracts -e ../agent-tools -e .

# 2. Register your domain in config/workspace.yaml
#    teams:
#      my-backend:
#        industry: ecommerce
#        reference: [{path: ../my-backend/src}]
#        target:    [{path: ../my-backend/src}, {path: ../my-backend/tests}]
#        docs:      [{path: ../my-backend/docs}]

# 3. Smoke test (zero API tokens)
./ai test smoke                          # full journey with SmokeTestProvider
./ai test smoke --domain ../my-backend   # health-check a generated domain

# 4. Real genesis build (needs LLM access — see PROVIDER_GUIDE.md)
export ANTHROPIC_AUTH_TOKEN=...
./ai genesis build --team my-backend

# 5. Run an agent or a workflow on a real task
./ai run workflow ../my-backend/workflows/feature_delivery.yaml \
  --task '{"feature": "Add product search by name and price range"}'
```

Full guide → **[docs/USER_GUIDE_END_TO_END.md](docs/USER_GUIDE_END_TO_END.md)**

---

## What problem does this solve?

Generic code-gen agents don't know your codebase. **Gentcore solves this by having a meta-agent layer (genesis) read your repo and write a domain-specific agent fleet for you.** The generated agents know your stack, your layout, your coding standards, your tools — because they were synthesized from those artifacts.

**Three layers:**
1. **Genesis agents** (`agents/_genesis/*`) — the factory. They scan, classify, design, and build domain agents.
2. **Shared stage agents** (`agents/_shared/*`) — ~20 reusable building blocks (CodeWriter, TestRunner, LinterAgent, etc.). Genesis wires these into domain workflows rather than always creating from scratch.
3. **Domain agents** (`../my-backend/agents/*`) — the generated fleet, specific to your repo, writing real code via real tools.

---

## What works today (verified against live LLMs)

| Provider | Status | Notes |
|----------|--------|-------|
| Anthropic native (Claude Opus / Sonnet / Haiku) | ✅ first-class | `submit_output` forced-structured output (H1) |
| Anthropic-compat gateways (Minimax M2.7, GLM 5.1 via BigModel) | ✅ | Set `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` |
| OpenAI native (GPT-5 family) | ✅ | tool use + JSON mode |
| OpenAI-compat (Gemini 3 Flash, DeepSeek, …) | ✅ | Automatic tool-schema + history translation |
| Claude Code subscription (`claude -p`) | ⚠ prose-only | No tool calls — good for planner/reviewer |
| AWS Bedrock | ✅ | bearer token + long-term creds |
| `DryRunProvider` | ✅ | Zero-cost pipeline smoke |
| `SmokeTestProvider` | ✅ | Schema-correct stubs — CI-friendly |

See **[docs/PROVIDER_GUIDE.md](docs/PROVIDER_GUIDE.md)** for per-provider setup.

---

## Runtime features

- **Self-healing DAG workflows** — gate conditions (`all_passed == true`, `approved == true`) trigger retry-with-feedback; feedback loops route back to upstream steps; `reset_points` checkpoint rollbacks.
- **6-layer permission merge (`RuleEngine`)** — platform rules are non-negotiable; teams can tighten, never loosen. Hot-reloadable from `config/rules.yaml`.
- **Schema-enforced output** — every agent declares `output_schema`; the validator + gate read fields directly (`all_passed`, `approved`, `files_changed`).
- **Typed observability** — per-step retry count, tokens, cost, duration logged as `ExecutionEvent`s.
- **Per-model prompt nudges** — opt-in hints for weaker models (Minimax M2, Gemini Flash, GLM) via `harness/providers/model_hints.py`. Strong models (Opus, Sonnet, GPT-5) get no bloat.
- **Trusted paths** — teams with `trusted: true` skip permission prompts for `file_read`.
- **Two synthesis modes** — `_factory/AgentFactoryAgent` for on-demand single-agent creation; `_genesis/AgentBuilderAgent` for full domain bootstrap. See [docs/FACTORY_VS_BUILDER.md](docs/FACTORY_VS_BUILDER.md).
- **Architect v2 catalog-driven** — default since Phase 2d. v2 designs from the `_shared` stage catalog + `capabilities.yaml`; opt-out via `GENTCORE_ARCHITECT_V2=0`.

---

## Key paths

| Path | What lives there |
|------|------------------|
| `harness/core/` | Engine — AgentRunner, CompositionEngine, RuleEngine, OutputValidator, OutputParser, AgentRegistry, PackIndex, RetryPolicy |
| `harness/core/expr.py` | Unified gate-condition evaluator (and/or/not, 6 comparators, dotpath, len, contains) |
| `harness/providers/` | Anthropic, OpenAI, Bedrock, ClaudeCode, DryRun, SmokeTest, **model_hints** |
| `harness/cli/ai.py` | `./ai` CLI entry point |
| `agents/_genesis/` | 8 genesis agents — the builders |
| `agents/_shared/` | ~20 reusable stage agents |
| `config/` | `rules.yaml`, `workspace.yaml`, `capabilities.yaml`, `industries.yaml` |
| `docs/` | All guides (see index below) |
| `scripts/` | Runnable examples — smoke + real-LLM test harnesses |

---

## Docs index

| Doc | Purpose |
|-----|---------|
| [USER_GUIDE_END_TO_END.md](docs/USER_GUIDE_END_TO_END.md) | **Start here.** Full user journey with live-LLM examples. |
| [QUICKSTART.md](docs/QUICKSTART.md) | Five-minute zero-to-running |
| [PROVIDER_GUIDE.md](docs/PROVIDER_GUIDE.md) | Setting up each LLM provider (native + compat gateways) |
| [AUTOMATION_GUIDE.md](docs/AUTOMATION_GUIDE.md) | Running agents automatically (CI, cron, webhooks) |
| [SYSTEM_GUIDE.md](docs/SYSTEM_GUIDE.md) | Deep architecture — every subsystem explained |
| [TEAM_GUIDE.md](docs/TEAM_GUIDE.md) | Operational guide for domain teams |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Component diagrams + data flows |
| [DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) | Production deployment patterns |
| [FACTORY_VS_BUILDER.md](docs/FACTORY_VS_BUILDER.md) | On-demand synthesis vs full domain genesis |
| [GITHUB_REPOS_GUIDE.md](docs/GITHUB_REPOS_GUIDE.md) | Multi-repo layout + contribution workflow |
| [FRAMEWORK_AUDIT_2026Q2.md](docs/FRAMEWORK_AUDIT_2026Q2.md) | Audit findings + Phase 2 fix log |

---

## Tests

```bash
.venv/bin/pytest harness/tests/ -q        # 1462 tests
./ai test smoke                           # full smoke journey (zero API tokens)
./ai test smoke --cross-domain            # backend+frontend cross-domain
./ai test smoke --domain PATH             # health check a generated domain
```

---

## Core commands

```bash
./ai workspace                                         # show registered domains + tool packs
./ai genesis scan   --team backend-team --dry-run
./ai genesis build  --team backend-team [--dry-run]
./ai run agent      <id> --task "..." --domain PATH [--dry-run]
./ai run workflow   PATH --task '{...}' [--dry-run]
./ai validate       PATH
./ai test smoke     [--preflight|--cross-domain|--domain PATH|--verbose]
```
