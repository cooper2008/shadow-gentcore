# Factory vs Builder — Role Split (B8)

Short reference clarifying when to use `_genesis/AgentBuilderAgent/v1` vs
`_factory/AgentFactoryAgent/v1`. Pre-B8 their descriptions overlapped
("creates all files for a complete domain" / "generates a complete domain
directory") and teams were unsure which to invoke in a given context.

## TL;DR

| | **_genesis/AgentBuilderAgent/v1** | **_factory/AgentFactoryAgent/v1** |
|---|---|---|
| Scope | FULL domain | ONE agent |
| Upstream | AgentArchitectAgent design | CapabilityRecipe `{goal, tools, context}` |
| Entry workflow | `workflows/genesis/genesis_build.yaml` | `workflows/factory/learn_and_create.yaml` |
| Triggered by | First-time genesis on a new domain | Architect v2 emits `decision: synthesize-new` for a roster slot |
| Output | Many files: agents/*, workflows/*, config/* | One `AgentName/v1/{agent_manifest.yaml, system_prompt.md, grading_criteria.yaml}` |
| Retry loop | QualityGate → Builder (feedback) | ValidateTestAgent → Factory (feedback) |

## When to use Builder

You are **bootstrapping a new domain** for the first time:

- Domain owner has pointed `domain.yaml` at source repos
- Genesis pipeline has run scan → map → discover → engineer_context → architect
- Now AgentBuilderAgent turns the architect's full design into a working
  directory tree: every agent manifest, every workflow, every config file.

This is the end-to-end genesis path. Builder is not for one-off additions.

## When to use Factory

You have an **existing domain** that already runs, and the architect has
flagged a gap:

- Architect v2 (B5) composes rosters from the `_shared/` stage catalog.
- For most stages `decision: reuse-core` or `reuse-with-prompt-override` — no
  new agent files needed.
- When no catalog stage fits and the user's workflow requires a novel
  capability, Architect v2 sets `decision: synthesize-new`.
- At that point the genesis orchestrator calls Factory with a
  CapabilityRecipe: `{goal: "what this agent must do", tools: [...],
  context: {standards, glossary, prior_outputs}}`.
- Factory emits ONE agent directory. The rest of the domain is untouched.

Factory is the T4-tier escape hatch in the audit's "Thin Stages + Rich Tools
+ Capability Map" model (§5). Most rosters should reach ≥ 70% catalog reuse;
Factory exists for the residual gap, not as the default path.

## Why the overlap used to confuse

Both agents read/write the same tree shape (`agents/<Name>/v1/*.yaml`) and
both operate in plan_execute mode with feedback loops. The overlap is
mechanical, not semantic: Builder does it at domain scale once, Factory
does it one agent at a time as-needed. This doc formalises that split so
future PRs and prompts can reference "Builder" and "Factory" with a single
meaning each.

## What did NOT change in B8

- Agent manifest IDs remain the same (`_genesis/AgentBuilderAgent/v1`,
  `_factory/AgentFactoryAgent/v1`). No rename, no version bump.
- Workflow wiring untouched. `workflows/genesis/genesis_build.yaml` still
  calls Builder; `workflows/factory/learn_and_create.yaml` still calls
  Factory. Existing genesis runs produce identical output.
- B8 is documentation + clarified descriptors — no runtime behaviour change.

## Future work flagged in the audit

- §2 B6 proposes converging the two workflows into one pipeline with
  branches for existing vs new domain. That's a separate audit item; B8
  only clarifies the role split so that convergence has a clean starting
  point.
