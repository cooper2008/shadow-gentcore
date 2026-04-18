# shadow-gentcore — Multi-Domain AI Agent Framework SDK

Core SDK for a 4-repo agent framework. See **[docs/USER_GUIDE_END_TO_END.md](docs/USER_GUIDE_END_TO_END.md)** to start; **[docs/SYSTEM_GUIDE.md](docs/SYSTEM_GUIDE.md)** for the deep dive.

## Quick Reference

```
agent-contracts ← shadow-gentcore ← agent-tools ← domain-* (e.g. acme-backend)
```

- **Agent = YAML manifest + system prompt** — no code per agent
- **Genesis agents** auto-generate domains: `./ai genesis build --team <name>`
- **3-Layer Knowledge**: standards.md + reference/*.md on-demand + tools
- **RuleEngine**: 6-layer permission merge, hot-reloadable, platform rules non-negotiable
- **AgentState lifecycle**: SPAWNING→READY→RUNNING→VALIDATING→COMPLETED/FAILED
- **Self-healing DAGs**: gates + feedback_loops + retry_with_feedback + reset_points
- **Model hints**: opt-in per-model nudges (`harness/providers/model_hints.py`)
- **Architect v2** catalog-driven (default); **DryRun/SmokeTest** providers need no API key

## Key Commands

```bash
./ai genesis build --team backend-team --dry-run   # Auto-generate domain
./ai run agent <id> --task "..." --domain <path>   # Run single agent
./ai run workflow <path> --dry-run                 # Run workflow
./ai test smoke [--domain PATH|--cross-domain]     # Zero-cost smoke test
./ai workspace                                     # Show status
```

## Key Paths

| Path | What |
|------|------|
| `harness/core/` | Engine — AgentRunner, CompositionEngine, RuleEngine, OutputValidator |
| `harness/core/expr.py` | Unified gate-condition evaluator |
| `harness/core/output_parser.py` | 4-strategy JSON extraction + type coercion |
| `harness/providers/` | Anthropic, OpenAI, Bedrock, ClaudeCode, DryRun, SmokeTest, model_hints |
| `harness/cli/ai.py` | CLI entry point |
| `agents/_genesis/` | 8 genesis agents (the builders) |
| `agents/_shared/` | ~20 reusable stage agents |
| `config/` | rules.yaml, workspace.yaml, capabilities.yaml, industries.yaml |
| `scripts/` | Runnable examples (smoke + real-LLM harnesses) |

## Docs index

- [QUICKSTART.md](docs/QUICKSTART.md) — 5-minute zero-to-running
- [USER_GUIDE_END_TO_END.md](docs/USER_GUIDE_END_TO_END.md) — full user journey
- [PROVIDER_GUIDE.md](docs/PROVIDER_GUIDE.md) — LLM provider setup
- [AUTOMATION_GUIDE.md](docs/AUTOMATION_GUIDE.md) — CI/cron/webhook patterns
- [SYSTEM_GUIDE.md](docs/SYSTEM_GUIDE.md) — architecture deep-dive
- [TEAM_GUIDE.md](docs/TEAM_GUIDE.md) — operational guide

## Testing

```bash
.venv/bin/pytest harness/tests/ -q    # 1462 tests
./ai test smoke                       # full journey (zero tokens)
```
