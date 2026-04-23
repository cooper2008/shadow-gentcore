# shadow-gentcore — Multi-Domain AI Agent Framework SDK

Core SDK for a 4-repo agent framework. See **[docs/USER_GUIDE_END_TO_END.md](docs/USER_GUIDE_END_TO_END.md)** to start; **[docs/SYSTEM_GUIDE.md](docs/SYSTEM_GUIDE.md)** for the deep dive.

## Quick Reference

```
agent-contracts ← shadow-gentcore ← agent-tools ← domain-* (e.g. acme-backend)
```

- **Agent = YAML manifest + system prompt** — no code per agent
- **Genesis agents** auto-generate domains: `./ai genesis build --domain <path>`
- **Tiered knowledge architecture** (replaces old "3-layer"):
  - **Tier 1** — `standards.md` always-injected (~500 lines)
  - **Tier 2** — `context_retrieve(topic, keywords)` → top-K chunks from `reference_index.yaml` (keyword-indexed, no embeddings)
  - **Tier 3** — `origin_fetch(path)` → live re-fetch from SourceAdapter cache (scope-guarded, audit-logged)
  - **Tier 4** — `memory_recall(key, k)` → past task outputs (FileMemoryStore JSONL). Auto-wired in production by `manifest_loader.build_memory_store`; override location via `GENTCORE_MEMORY_DIR`.
  - **Tier 5** — EvolutionAgent (single-shot + preload) consumes `domain_evolution_signals` aggregated by `harness/core/evolution_signals.py` (origin hotspots, citation weaknesses, memory patterns). Runs via `workflows/genesis/genesis_evolve.yaml`.
- **Source adapters** — genesis reads from `github://org/repo@ref`, local paths, (+skeletons for Confluence/Notion/Jira). See `docs/SOURCE_ADAPTERS.md`.
- **Credential auto-propagation** — Builder derives `required_credentials:` per agent from tool packs; writes `REQUIRED_CREDENTIALS.md`; OAuth groups handled (`./ai credentials oauth-setup <group>`). See `docs/CREDENTIALS_GUIDE.md`.
- **ToolSynthesizerAgent** — closes tool gaps via known MCP servers or synthesized packs, gated by a static security scanner.
- **Prompt ↔ manifest contract validator** — `./ai validate-contracts --domain <path>` catches drift between what the prompt promises and what the manifest declares.
- **RuleEngine**: 6-layer permission merge, hot-reloadable, platform rules non-negotiable
- **AgentState lifecycle**: SPAWNING→READY→RUNNING→VALIDATING→COMPLETED/FAILED
- **Self-healing DAGs**: gates + feedback_loops + retry_with_feedback + reset_points
- **Multi-workflow per domain**: triage.yaml dispatches via router gate, one /run endpoint
- **Model hints**: opt-in per-model nudges (`harness/providers/model_hints.py`); Gemini 3 `thought_signature` auto-preserved
- **Architect v2** catalog-driven (default); **DryRun/SmokeTest** providers need no API key
- **Emit-then-write Builder**: single-turn LLM call emits `files: [{path, content}]`, Python hook writes them. No multi-turn `file_write` hang.

## Key Commands

```bash
./ai genesis build --domain <path>               # Auto-generate domain (8-step pipeline)
./ai run agent <id> --task "..." --domain <path> # Run single agent
./ai run workflow <path> --domain <path>         # Run workflow
./ai test smoke [--domain PATH|--cross-domain]   # Zero-cost smoke test
./ai validate-contracts --domain <path>          # Prompt ↔ manifest drift check
./ai credentials status --domain <path>          # Per-agent credential resolution
./ai credentials oauth-setup <group>             # Print OAuth setup steps
./ai workspace                                   # Show status
```

## Key Paths

| Path | What |
|------|------|
| `harness/core/` | Engine — AgentRunner, CompositionEngine, RuleEngine, OutputValidator |
| `harness/core/expr.py` | Unified gate-condition evaluator (with success/completed synonym) |
| `harness/core/output_parser.py` | 4-strategy JSON extraction + type coercion |
| `harness/core/context_retriever.py` | Tier 2 — keyword-indexed chunk search |
| `harness/core/memory_store.py` | Tier 4 — FileMemoryStore + InMemoryMemoryStore |
| `harness/core/source_adapters/` | URI-scheme dispatch: file://, github://, confluence://, notion://, jira:// |
| `harness/core/credential_registry.py` | Credential declarations + resolution + OAuth groups |
| `harness/core/tool_security_scanner.py` | Static scanner for synthesized packs |
| `harness/core/prompt_contract_validator.py` | Prompt ↔ manifest drift check |
| `harness/providers/` | Anthropic, OpenAI, Bedrock, ClaudeCode, DryRun, SmokeTest, model_hints |
| `harness/tools/builtin.py` | Built-in tool adapters incl. `context_retrieve`, `origin_fetch`, `memory_recall` |
| `harness/cli/ai.py` | CLI entry point |
| `agents/_genesis/` | 13 genesis agents: Scanner, Mapper, **ConflictResolver**, **ContextVerifier**, ContextEngineer, ToolDiscovery, ToolSynthesizer, Architect (v1+v2), Builder, QualityGate, **DomainPlanner**, **HouseStyle**, Evolution |
| `agents/_shared/` | ~23 reusable stage agents |
| `config/` | rules.yaml, workspace.yaml, capabilities.yaml, industries.yaml, known_mcp_servers.yaml, tool_security.yaml |

## Genesis pipeline (10 steps)

```
scan → map → resolve → {discover_tools, engineer_context}
          → verify → synthesize_tools → architect → build → validate
```

Single-shot where possible (preloaded context instead of react), emit-then-write
for file producers. Coverage-aware gates at every layer; feedback loops verify→context,
validate→build, validate→context. Adjacent workflows: `genesis_scan.yaml`
(quick audit), `genesis_org_plan.yaml` (org-level roadmap), `maintenance/house_style_sync.yaml`.

## Docs index

- [QUICKSTART.md](docs/QUICKSTART.md) — 5-minute zero-to-running
- [USER_GUIDE_END_TO_END.md](docs/USER_GUIDE_END_TO_END.md) — full user journey
- [MEMORY_GUIDE.md](docs/MEMORY_GUIDE.md) — Tier 1-5 memory architecture
- [SOURCE_ADAPTERS.md](docs/SOURCE_ADAPTERS.md) — reading from GitHub / Confluence / Notion / Jira
- [CREDENTIALS_GUIDE.md](docs/CREDENTIALS_GUIDE.md) — API keys + OAuth2 groups, auto-propagation
- [PROVIDER_GUIDE.md](docs/PROVIDER_GUIDE.md) — LLM provider setup (incl. Gemini 3.1 Pro)
- [AUTOMATION_GUIDE.md](docs/AUTOMATION_GUIDE.md) — CI/cron/webhook patterns
- [SYSTEM_GUIDE.md](docs/SYSTEM_GUIDE.md) — architecture deep-dive
- [TEAM_GUIDE.md](docs/TEAM_GUIDE.md) — operational guide

## Testing

```bash
.venv/bin/pytest harness/tests/ -q    # 1950 tests
./ai test smoke                       # full journey (zero tokens)
./ai test smoke --cross-domain        # backend + frontend in parallel
./ai validate-contracts --domain X    # prompt ↔ manifest drift
./ai validate-contracts --domain X --llm-judge   # + semantic drift via LLM judge
```
