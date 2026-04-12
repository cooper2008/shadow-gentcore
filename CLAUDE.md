# shadow-gentcore — Multi-Domain AI Agent Framework SDK

Core SDK for a 4-repo agent framework. See **[docs/SYSTEM_GUIDE.md](docs/SYSTEM_GUIDE.md)** for full documentation.

## Quick Reference

```
agent-contracts ← shadow-gentcore ← agent-tools ← domain-*
```

- **Agent = YAML manifest + system prompt** — no code per agent
- **Genesis agents** auto-generate domains from repos: `./ai genesis build --team <name>`
- **3-Layer Knowledge**: standards.md (always injected) + reference/*.md (on-demand) + tools (~10 generic)
- **RuleEngine**: 6-layer permission merge, hot-reloadable, platform rules non-negotiable
- **AgentState lifecycle**: SPAWNING → READY → RUNNING → VALIDATING → COMPLETED/FAILED (all agents)
- **Typed ExecutionEvent**: enum-based workflow events for observability (all workflows)
- **Trusted paths**: teams with `trusted: true` skip permission prompts for file_read
- **DryRunProvider**: `--dry-run` on all commands, no API key needed

## Key Commands

```bash
./ai genesis build --team backend-team --dry-run   # Auto-generate domain
./ai run agent <id> --task "..." --domain <path>    # Run single agent
./ai run workflow <path> --dry-run                  # Run workflow
./ai workspace                                      # Show status
```

## Key Paths

| Path | What |
|------|------|
| `harness/core/` | Engine: AgentRunner, CompositionEngine, RuleEngine, OutputValidator |
| `harness/core/output_parser.py` | OutputParser — 4-strategy JSON extraction + type coercion |
| `harness/core/tool_disclosure.py` | ToolDisclosureRouter — L1/L2 progressive tool disclosure |
| `harness/cli/ai.py` | CLI entry point |
| `agents/_genesis/` | 8 genesis agents (the builders) |
| `agents/_shared/` | 20 reusable stage agents |
| `config/` | rules.yaml, workspace.yaml, genesis_rules.yaml |
| `docs/SYSTEM_GUIDE.md` | Full documentation |
| `docs/TEAM_GUIDE.md` | Team operational guide |

## Testing

```bash
.venv/bin/pytest harness/tests/ -q    # 862 tests
```
