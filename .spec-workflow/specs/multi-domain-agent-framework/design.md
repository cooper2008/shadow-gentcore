# Design

This design defines a multi-domain agent platform whose core harness, typed contracts, tool adapters, authoring kit, and workflow engine let independently authored team domains interoperate safely with progressive autonomy.

## 1. Design Overview

The framework is designed as a **platform** rather than a fixed set of built-in agents. It uses a **multi-repo architecture** with four repositories: a shared contracts package (`agent-contracts`), a framework SDK (`shadow-gentcore`), a shared tool library (`agent-tools`), and team-owned domain repos (`domain-*`). The platform team owns the harness core, shared contracts, templates, validation, certification, runtime abstractions, and cross-domain composition model. Domain teams own their own domains, packs, atomic agents, workflows, tool bindings, and grading criteria in separate repositories.

A core principle is **Agent = Configuration, Not Code**. There is one `AgentRunner` class that runs any agent. An agent's identity, capabilities, reasoning mode, and constraints are defined entirely through configuration manifests — not through Python subclasses. The same runner becomes a codegen agent, a PM agent, or a testing agent based solely on its manifest bundle.

The design intentionally separates six concepts:
- **Domain**: bounded business context such as `backend`, `qa`, or `incident`
- **Capability Pack**: reusable bundle of atomic agents inside a domain
- **Atomic Agent**: smallest independently runnable worker, defined by a manifest bundle
- **Workflow**: ordered or DAG execution of packs and/or agents with quality gates
- **Harness Pattern**: reusable execution wrapper such as evaluator loop, context reset, or reasoning mode
- **Tool Pack**: reusable collection of tool adapters that can be shared across agents and domains

This separation removes the ambiguity that existed when "pipeline" was used to mean both business workflow and execution primitive.

## 2. Design Goals

The design must satisfy the approved requirements by delivering:
- a predictable repository structure and discovery model
- a golden-path authoring flow for teams
- typed contracts for agent interoperability
- strict domain isolation with controlled cross-domain composition
- local/cloud runtime parity
- progressive autonomy and policy enforcement
- tool and integration extensibility without harness-core rewrites
- repository-local knowledge, runtime legibility, and mechanical enforcement as first-class platform capabilities
- observability, cost control, certification, and maintenance workflows

## 3. Architectural Principles

1. **Harness owns execution** — agents never call each other directly.
2. **Typed contracts over prompt conventions** — all interoperability flows through manifests, schemas, ports, artifacts, and checkpoints.
3. **Platform, not bundle** — the platform team provides standards and tooling; domain teams contribute domains.
4. **Golden path first** — scaffolding, validation, certification, and publishing are the easiest way to contribute.
5. **Isolation before composition** — each domain is a bounded context with explicit read/write/tool policies.
6. **Progressive autonomy** — domains mature from assisted to guarded autonomous to workflow autonomous.
7. **Pluggable tools** — required tools are attached declaratively through tool manifests/adapters, not hardcoded inside prompts.
8. **Legibility before autonomy** — logs, artifacts, screenshots, traces, and docs must be visible before workflows are trusted.
9. **Repository-local knowledge is the operating environment** — `AGENTS.md` and `docs/` are the discoverable source of truth for future agents.
10. **Mechanical enforcement over prose** — architecture boundaries, doc freshness, and topology rules are checked automatically.
11. **Stage-based defaults with runtime override** — execution modes should be sensible by default yet adjustable for quality/cost trade-offs.
12. **Agent = Configuration, Not Code** — there is one AgentRunner; an agent's identity comes from its manifest bundle (prompt, tools, schemas, mode, category, constraints), not from Python subclasses.
13. **Multi-repo ownership** — the SDK, shared contracts, shared tools, and domain agents live in separate repositories with independent release cadences.
14. **Language-agnostic agents** — the harness SDK is Python, but agents can work with any target language through language-specific tool packs and prompts.
15. **Layered quality loops** — quality is enforced at five levels: inner execution loop, self-critique/reflexion, gate retry, cross-stage feedback, and workflow-level evaluator loop.
16. **4-layer configuration override** — category defaults → agent manifest → workflow step override → runtime override, with deep-merge semantics.

## 4. Multi-Repo Architecture

The framework uses four repositories with clear ownership boundaries and a single dependency direction.

### 4.1 Four-Repo Model

| Repo | Purpose | Dependencies |
|---|---|---|
| `agent-contracts` | Shared types: manifests, runtime contracts, enums | None (pure Python / Pydantic) |
| `shadow-gentcore` | Framework SDK: engine, providers, templates, authoring, CLI | `agent-contracts` |
| `agent-tools` | Shared tool library: adapters, packs, resolver | `agent-contracts` |
| `domain-*` | Team-owned domains: agents, workflows, context | `agent-contracts`, `shadow-gentcore`, `agent-tools` |

Dependency graph:
```text
agent-contracts  ←── shadow-gentcore (SDK)
       ↑         ←── agent-tools
       ↑         ←── domain-backend
       ↑         ←── domain-qa
       └──────── ←── domain-*
```

### 4.2 Repo 1: agent-contracts (Shared Types)

A zero-dependency lightweight package containing all type definitions used across repos.

```text
agent-contracts/
├── pyproject.toml
├── src/agent_contracts/
│   ├── manifests/
│   │   ├── domain_manifest.py      — DomainManifest
│   │   ├── agent_manifest.py       — AgentManifest
│   │   ├── pack_manifest.py        — CapabilityPackManifest
│   │   ├── workflow_def.py         — WorkflowDefinition
│   │   ├── tool_manifest.py        — ToolManifest
│   │   └── tool_pack_manifest.py   — ToolPackManifest
│   ├── contracts/
│   │   ├── task_envelope.py        — TaskEnvelope
│   │   ├── run_record.py           — RunRecord
│   │   ├── artifact_record.py      — ArtifactRecord
│   │   ├── checkpoint.py           — Checkpoint
│   │   ├── feature_contract.py     — FeatureContract
│   │   └── port_binding.py         — PortBinding
│   ├── categories.py               — Category enum + defaults
│   └── execution_modes.py          — Mode enum + config types
└── tests/
```

### 4.3 Repo 2: shadow-gentcore (Framework SDK)

```text
shadow-gentcore/
├── AGENTS.md
├── config/
│   ├── categories.yaml
│   ├── domains.yaml                  — domain discovery config
│   └── environments/
│       ├── local.yaml
│       └── cloud.yaml
├── docs/
│   ├── ARCHITECTURE.md
│   ├── PLANS.md
│   ├── QUALITY_SCORE.md
│   ├── RELIABILITY.md
│   ├── SECURITY.md
│   ├── design-docs/
│   ├── exec-plans/
│   │   ├── active/
│   │   ├── completed/
│   │   └── tech-debt-tracker.md
│   └── references/
├── agents/
│   ├── _orchestrator/
│   │   ├── PlannerAgent/v1/
│   │   ├── EvaluatorAgent/v1/
│   │   ├── OrchestratorAgent/v1/
│   │   └── ReviewAgent/v1/
│   └── _maintenance/
│       ├── DocGardenerAgent/v1/
│       ├── QualityScoreAgent/v1/
│       └── DriftCleanupAgent/v1/
├── workflows/
│   ├── cross_domain/
│   └── maintenance/
├── harness/
│   ├── core/
│   │   ├── agent_runner.py           — single-agent turn loop
│   │   ├── prompt_assembler.py       — manifest → assembled prompt
│   │   ├── mode_dispatcher.py        — execution strategy selection
│   │   ├── tool_executor.py          — tool call execution
│   │   ├── composition_engine.py     — workflow executor
│   │   ├── evaluator_loop.py         — planner→generator→evaluator
│   │   ├── grading_engine.py         — automated + LLM grading
│   │   ├── context_engine.py         — context build, compaction, reset
│   │   ├── handoff.py                — durable checkpoints
│   │   ├── message_bus.py            — typed inter-agent communication
│   │   ├── guardrails.py             — schema, command, path validation
│   │   ├── permissions.py            — runtime permission resolution
│   │   ├── budget_tracker.py         — token/cost/time tracking
│   │   ├── validation_pipeline.py    — lint/test/coverage checks
│   │   ├── agent_registry.py         — manifest discovery and loading
│   │   └── runtime.py                — base runtime + local/cloud
│   ├── providers/
│   │   ├── router.py                 — category-based LLM routing
│   │   ├── anthropic_adapter.py
│   │   ├── openai_adapter.py
│   │   └── bedrock_adapter.py
│   ├── tools/
│   │   └── resolver.py               — tool:// and toolpack:// resolution
│   ├── authoring/
│   │   ├── scaffolder.py
│   │   ├── validator.py
│   │   ├── certifier.py
│   │   ├── compatibility.py
│   │   └── publisher.py
│   ├── templates/
│   │   └── agent/                    — base agent template
│   │       ├── system_prompt.md.tmpl
│   │       ├── tools.yaml.tmpl
│   │       ├── input_schema.json.tmpl
│   │       ├── output_schema.json.tmpl
│   │       ├── constraints.yaml.tmpl
│   │       ├── permissions.yaml.tmpl
│   │       └── grading_criteria.yaml.tmpl
│   ├── bridges/
│   │   ├── browser_bridge.py
│   │   └── observability_bridge.py
│   ├── cli/
│   ├── lints/
│   │   └── architecture_lints/
│   └── tests/
├── examples/
│   └── backend/                      — minimal reference domain (E2E proof)
│       ├── domain.yaml
│       ├── agents/
│       ├── workflows/
│       └── context/
└── .spec-workflow/
```

### 4.4 Repo 3: agent-tools (Shared Tool Library)

```text
agent-tools/
├── pyproject.toml
├── src/agent_tools/
│   ├── adapters/
│   │   ├── cli_adapter.py            — shell command execution
│   │   ├── mcp_adapter.py            — MCP server integration
│   │   └── http_api_adapter.py       — HTTP/API integration
│   ├── packs/
│   │   ├── python_build.yaml         — pytest, mypy, ruff, pip
│   │   ├── java_build.yaml           — maven, gradle, checkstyle
│   │   ├── go_build.yaml             — go test, golangci-lint
│   │   ├── build_core.yaml           — language-agnostic build tools
│   │   ├── github_pr.yaml            — GitHub PR tools
│   │   ├── browser.yaml              — browser/UI tools
│   │   └── observability.yaml        — logs/metrics/traces
│   └── resolver.py                   — resolve tool:// and toolpack://
└── tests/
```

### 4.5 Repo 4+: domain-* (Team-Owned Domains)

Each domain team creates a repo following the SDK structure:

```text
domain-backend/
├── domain.yaml                       — manifest (owner, ports, policy)
├── agents/
│   ├── CodeGenAgent/v1/
│   │   ├── agent_manifest.yaml
│   │   ├── system_prompt.md
│   │   ├── tools.yaml
│   │   ├── input_schema.json
│   │   ├── output_schema.json
│   │   ├── constraints.yaml
│   │   ├── permissions.yaml
│   │   └── grading_criteria.yaml
│   ├── ValidateAgent/v1/
│   ├── TestAgent/v1/
│   └── ReviewAgent/v1/
├── packs/
│   └── build.yaml
├── workflows/
│   └── quick_change.yaml
├── context/                          — domain-specific reference docs
├── tools/                            — domain-specific tool overrides
└── tests/
```

### 4.6 Why Multi-Repo

| Concern | Single Repo Problem | Multi-Repo Benefit |
|---|---|---|
| Tool updates | Changing a tool adapter forces framework release | Tools versioned independently |
| Domain updates | Updating one domain's agents touches framework | Teams own their repo, merge on their schedule |
| Onboarding | New teams must understand the whole monolith | Teams only need SDK docs + their domain repo |
| Context docs | Domain reference docs clutter framework | Each domain repo has its own `context/` |
| Release cadence | Everything ships together or not at all | Framework, tools, and domains release independently |
| Type safety | Domain repos depend on full SDK for types | Lightweight `agent-contracts` for types only |

### 4.7 Domain Discovery

The SDK discovers domain repos at runtime through configuration:

```yaml
# config/domains.yaml
domains:
  - path: /path/to/domain-backend
  - path: /path/to/domain-qa
  - package: domain-security    # pip-installed domain
  - path: ./examples/backend    # local example domain
```

### 4.8 Repository Knowledge, Legibility, and Enforcement

The framework treats the repository as the primary operating environment for agents:

- `AGENTS.md` is a short map, not a giant instruction dump.
- `docs/` stores architecture, plans, quality scorecards, reliability guidance, security guidance, references, and execution plans.
- `docs/exec-plans/active` and `docs/exec-plans/completed` preserve ongoing and historical execution plans for future agents.
- `worktree_manager.py` provisions isolated worktrees/sandboxes per run.
- `browser_bridge.py` and `observability_bridge.py` make UI state, logs, metrics, and traces inspectable.
- `architecture_lints/` and doc-lint rules mechanically enforce topology, dependency direction, doc freshness, and structural standards.

Autonomy depends on legibility: if an agent cannot inspect evidence in-repo or at runtime, the system should assume the evidence does not effectively exist.

## 5. Core Domain Model

The design introduces the following first-class contracts.

### 5.1 Manifests and Definitions

| Contract | Purpose | Owned By |
|---|---|---|
| `DomainManifest` | Domain owner, purpose, workspace policy, ports, autonomy profile, default tool packs | Domain team |
| `CapabilityPackManifest` | Named reusable bundle of atomic agents and shared tool references | Domain team |
| `AgentManifest` | Agent identity, version, category, capabilities, schemas, permissions, tool bindings | Agent author |
| `WorkflowDefinition` | Sequence/DAG definition of packs/agents, gates, dependencies, reset points, budgets | Domain team / platform team |
| `ToolManifest` | Reusable tool declaration describing adapter type, auth mode, policy, output normalization | Platform or domain team |
| `ToolPackManifest` | Reusable collection of tool manifests and policy defaults | Platform or domain team |

### 5.2 Runtime Contracts

| Contract | Purpose |
|---|---|
| `TaskEnvelope` | Incoming work envelope with routing metadata, budget, timeout, and input payload |
| `ArtifactRecord` | Structured record for generated files, reports, screenshots, tool results, or normalized outputs |
| `Checkpoint` | Durable handoff artifact for context reset and workflow resumption |
| `RunRecord` | Execution metadata for agent/workflow/provider/tool usage, cost, duration, and status |
| `FeatureContract` | Planner-authored PASS/FAIL criteria used by evaluator loops |
| `PortBinding` | Compatibility binding between `provides` and `consumes` ports across domains/workflows |

### 5.3 Versioning Rules

- All manifests, workflows, agent versions, tool manifests, and schemas are versioned.
- Compatibility checks run before execution and before publication.
- Breaking schema or port changes require explicit version bumps.
- Reusable workflows must declare compatible manifest/schema ranges.

### 5.4 Platform-Owned Agent Namespaces

The platform reserves two agent namespaces that are not owned by any single business domain:

| Namespace | Agents | Responsibility |
|---|---|---|
| `_orchestrator` | `PlannerAgent`, `EvaluatorAgent`, `OrchestratorAgent`, `ReviewAgent` | Cross-domain planning, quality evaluation, routing, and human-review orchestration |
| `_maintenance` | `DocGardenerAgent`, `QualityScoreAgent`, `DriftCleanupAgent` | Repository hygiene, quality score updates, and entropy management |

These namespaces ensure orchestration and maintenance logic remain platform concerns rather than leaking into business domains.

## 6. Tool and Integration Model

This section addresses the requirement that agents must be able to add tools easily.

### 6.1 Tool Adapter Classes

The platform supports three standard adapter classes:

| Adapter Class | Use Cases | Examples |
|---|---|---|
| `cli` | Local or containerized command execution | build/test commands, linters, package managers, docker, terraform |
| `mcp` | External MCP servers exposed as tools | browser automation, IDE actions, specialized enterprise tools |
| `http_api` | SaaS/API integrations with structured auth and rate limits | GitHub, Jira, Slack, observability APIs |

Future adapter classes can be added, but these three cover the main adoption path.

### 6.2 Tool Declaration Model

Tools are not embedded only in prompts. They are declared in manifests and bound by policy.

```yaml
agent:
  id: BuildAgent
  version: v1
  tools:
    - ref: toolpack://platform/build-core
    - ref: tool://platform/github/pr-write
    - ref: tool://domain/backend/build-cli
```

A tool declaration resolves to:
- adapter class (`cli`, `mcp`, `http_api`)
- runtime availability requirements
- credential source
- timeout/retry/rate limit policy
- audit/logging rules
- output normalization contract

### 6.3 Tool Packs

Teams often need the same groups of tools. The platform therefore supports reusable tool packs, for example:
- `platform/build-core`
- `platform/github-pr`
- `platform/browser-validation`
- `platform/observability-read`
- `domain/backend-build`
- `domain/devops-cloudops`

Tool packs reduce repetition and make certification easier because common policies are reused.

### 6.4 Tool Lifecycle

1. Author declares tool refs in agent or pack manifest.
2. Validator resolves tool manifests and verifies availability.
3. Runtime resolves credentials and environment-specific endpoints.
4. Guardrails enforce per-tool policy.
5. Tool results are normalized into `ArtifactRecord` or typed workflow output.
6. Certification verifies safe use in local and cloud runtimes.

### 6.5 Tool Output Normalization

All tool outputs must become framework-readable data, not hidden prompt-only state.

Examples:
- CLI test runs -> `ArtifactRecord(type=test_report)`
- GitHub PR creation -> `ArtifactRecord(type=github_pr)`
- MCP browser snapshot -> `ArtifactRecord(type=ui_snapshot)`
- Observability API query -> typed `incident_context` payload

This allows downstream workflows and evaluators to reason over outputs deterministically.

## 7. Runtime Architecture

### 7.1 High-Level Flow

```text
TaskEnvelope
   ↓
WorkflowExecutor (CompositionEngine)
   ↓
Per-step: AgentRunner
   ├── PromptAssembler.build(manifest, tools, context, task)
   ├── ModeDispatcher.select(execution_mode) → ExecutionStrategy
   ├── ExecutionStrategy.run() → turn loop (LLM ↔ ToolExecutor)
   ├── GradingEngine.grade() → self-critique loop (if reflection enabled)
   ├── OutputValidator.validate()
   └── ArtifactCollector.collect()
   ↓
Gate checks → pass/retry/abort/escalate
   ↓
Cross-stage feedback loops (if configured)
   ↓
Evaluator loop (if enabled, wraps entire workflow)
   ↓
Final: RunRecords + ArtifactRecords + Checkpoints
```

### 7.2 Core Modules

| Module | Responsibility |
|---|---|
| `agent_runner.py` | Single-agent turn loop: init → prompt assembly → execution → output |
| `prompt_assembler.py` | Combines manifest system_prompt + resolved tools + constraints + context + task into final LLM prompt. **This is the key mechanism that makes one runner become any agent.** |
| `mode_dispatcher.py` | Selects execution strategy (ReAct, PlanExecute, CoT, SelfAsk, ToT) based on manifest `execution_mode` config |
| `tool_executor.py` | Resolves tool calls from LLM output, runs through adapters, normalizes output, reports back to runner |
| `grading_engine.py` | Scores agent output against `grading_criteria.yaml` using automated checks (tools) and LLM-judge criteria |
| `budget_tracker.py` | Tracks token usage, cost, and wall-clock time; enforces limits per agent and per workflow |
| `composition_engine.py` | `WorkflowExecutor` for sequence/DAG execution with gates, feedback loops, and evaluator wrapping |
| `evaluator_loop.py` | Planner → Generator → Evaluator wrapper for workflow-level quality iteration |
| `context_engine.py` | Context build, priority ranking, token compaction, reset policy, handoff orchestration |
| `agent_registry.py` | Load domain, pack, agent, workflow, and tool manifests from discovered domains |
| `runtime.py` | Base runtime abstraction plus local/cloud implementations |
| `worktree_manager.py` | Per-run isolated workspace/worktree bootstrap |
| `handoff.py` | Durable checkpoints and resumable state |
| `message_bus.py` | Typed inter-agent/workflow communication |
| `guardrails.py` | Schema validation, command policy, path bounds, content risk checks |
| `permissions.py` | Runtime permission resolution and audit decisions |
| `validation_pipeline.py` | Lint/test/coverage/structural validation and review hooks |
| `browser_bridge.py` | UI/browser inspection and evidence capture |
| `observability_bridge.py` | Logs/metrics/traces read access |

### 7.3 Harness Internal Architecture: AgentRunner.run()

The core execution path that transforms a manifest into a running agent:

```text
AgentRunner.run(task: TaskEnvelope, manifest: AgentManifest)
│
├─ 1. INIT
│   ├─ ManifestLoader.load(manifest_path) → prompt, tools, schemas, constraints, permissions, grading
│   ├─ InputValidator.validate(task.input_payload, manifest.input_schema)
│   ├─ ToolResolver.resolve(manifest.tools) → [resolved tool definitions]
│   ├─ ProviderRouter.select(manifest.category, task.budget) → LLM client
│   ├─ ModeDispatcher.select(manifest.execution_mode) → ExecutionStrategy
│   ├─ WorktreeManager.create_workspace(task.task_id, manifest.domain)
│   ├─ ContextEngine.initialize(manifest, task, workspace)
│   └─ RunRecord.create(status=started)
│
├─ 2. PROMPT ASSEMBLY (★ key step: one runner = any agent)
│   └─ PromptAssembler.build(manifest, resolved_tools, context, task)
│       SYSTEM MESSAGE:
│       ┌─ system_prompt.md (from manifest — agent identity)
│       ├─ resolved tool descriptions (agent capabilities)
│       ├─ constraints + permissions (agent boundaries)
│       └─ domain context (agent knowledge)
│       USER MESSAGE:
│       └─ task input payload (from TaskEnvelope)
│
├─ 3. EXECUTION LOOP (driven by ExecutionStrategy)
│   ├─ ReActStrategy: think → act → observe → repeat
│   ├─ PlanExecuteStrategy: plan first → execute steps sequentially
│   ├─ ChainOfThoughtStrategy: single deep reasoning pass
│   ├─ SelfAskStrategy: decompose into sub-questions
│   └─ TreeOfThoughtStrategy: explore parallel branches
│   Each turn: prompt → LLM → parse → tool exec → guardrails → context update → termination check
│   If reflection enabled: GradingEngine wraps with self-critique loop
│
├─ 4. OUTPUT
│   ├─ OutputValidator.validate(output, manifest.output_schema)
│   ├─ ArtifactCollector.collect(workspace_changes, tool_outputs)
│   ├─ Checkpoint.create() if workflow needs handoff
│   └─ RunRecord.finalize(status, cost, duration, tokens, artifacts)
│
└─ Return (RunRecord, List[ArtifactRecord])
```

### 7.4 PromptAssembler: How Configuration Becomes Identity

The PromptAssembler is the critical component that makes one `AgentRunner` class serve any agent role. It combines six sources into a single assembled prompt:

| Source | What It Contributes | Effect |
|---|---|---|
| `system_prompt.md` | Agent identity and role description | Makes the runner a codegen / PM / test agent |
| Resolved tool descriptions | Available tool names, signatures, usage docs | Gives the agent its capabilities |
| `constraints.yaml` | Path bounds, forbidden commands, file limits | Bounds what the agent can do |
| `permissions.yaml` | Allow/ask/deny rules per action type | Controls permission model |
| Domain context (from ContextEngine) | Relevant code files, architecture docs, prior artifacts | Gives the agent knowledge |
| Task input (from TaskEnvelope) | The actual work request | Gives the agent its current task |

The same PromptAssembler code runs for every agent. The manifest changes → the assembled prompt changes → different agent behavior.

### 7.5 ModeDispatcher: How Agents Think Differently

The ModeDispatcher selects the execution strategy based on `execution_mode` config. Each strategy implements a different turn loop structure:

| Strategy | Loop Structure | Best For |
|---|---|---|
| `ReActStrategy` | think → act → observe → repeat until done | Tool-heavy iterative work |
| `PlanExecuteStrategy` | create plan → execute steps in order | Structured artifact creation |
| `ChainOfThoughtStrategy` | single deep reasoning pass, no tool loop | Review and analysis |
| `SelfAskStrategy` | decompose into sub-questions, answer each | Systematic analysis |
| `TreeOfThoughtStrategy` | explore N branches in parallel, pick best | Critical design decisions |

All strategies use the same ProviderRouter, ToolExecutor, Guardrails, and ContextEngine. Only the loop structure differs.

### 7.6 SDK Public API

The framework exports these classes for domain teams and operators:

```python
from shadow_gentcore import (
    AgentRunner,           # Run a single agent from a manifest
    CompositionEngine,     # Run a workflow (sequence/DAG of agents)
    ManifestRegistry,      # Discover and load manifests from domains
    ProviderRouter,        # Route to LLM providers by category
    LocalRuntime,          # Local execution runtime
    CloudRuntime,          # Cloud execution runtime
    ToolResolver,          # Resolve tool:// and toolpack:// references
    PromptAssembler,       # Build prompts from manifests (for testing/debugging)
    ReplayHarness,         # Test agents with recorded LLM responses
)
```

### 7.7 Local vs Cloud Runtime

| Capability | LocalRuntime | CloudRuntime |
|---|---|---|
| Interactivity | Optional human review | Non-interactive by default |
| Workspace | Persistent local workspace | Ephemeral or mounted workspace |
| Credentials | env / local secrets | secret manager / runtime bindings |
| Output | console + files | structured JSON + webhook + uploaded artifacts |
| Permission handling | ask/allow/deny can pause | fail-closed or policy escalation |
| Context reset | optional/manual inspection | workflow-defined automatic reset |

## 8. Domain Isolation and Composition

### 8.1 Isolation Layers

1. **Workspace isolation** — write access limited to domain paths.
2. **Permission isolation** — only allowed commands, tools, and integrations may run.
3. **Schema isolation** — only validated typed inputs cross boundaries.
4. **Context isolation** — workflows start from minimized structured state, not arbitrary chat history.
5. **Runtime isolation** — violations are blocked and logged.

### 8.2 Composition Model

Cross-domain workflows compose domains through typed ports.

Example:
- `pm/feature_planning` provides `story_breakdown`
- `backend/feature` consumes `story_breakdown`, provides `code`, `api_spec`, `test_report`
- `qa/feature_verification` consumes `code` and `api_spec`, provides `qa_results`
- `security/application_audit` consumes `code`, provides `security_findings`
- `release/candidate` consumes prior outputs and enforces gates

Composition happens at the workflow or pack level first. Raw atomic agent wiring is reserved for internal domain workflows or advanced cases.

### 8.3 Domain Catalog (Reference)

The framework supports any number of team-owned domains. These are reference examples, not SDK implementation targets. Each lives in its own `domain-*` repo:

| Domain | Focus Area | Repo |
|---|---|---|
| `backend` | Backend dev, validation, testing, migration, deploy | `domain-backend` |
| `frontend` | UI generation, styling, accessibility, UI testing | `domain-frontend` |
| `qa` | Test strategy, automation, regression, E2E | `domain-qa` |
| `security` | SAST, SCA, secrets, compliance scanning | `domain-security` |
| `devops` | CI/CD, infrastructure, deployment, monitoring | `domain-devops` |
| `pm` | Planning, tracking, reporting, estimation | `domain-pm` |
| `incident` | Detection, triage, root cause, postmortem | `domain-incident` |
| `docs` | Technical writing, changelogs, diagrams | `domain-docs` |
| `data` | ETL, pipelines, schema evolution, analytics | `domain-data` |
| `release` | Versioning, release notes, feature flags | `domain-release` |
| `compliance` | Policy audit, privacy, access review | `domain-compliance` |

The SDK repo includes only `_orchestrator` and `_maintenance` platform namespaces plus a minimal `examples/backend` for E2E proof.

### 8.4 Domain Rollout Priority

| Priority | What | Where |
|---|---|---|
| `P0` | Example backend domain (4 agents, 1 workflow) | `shadow-gentcore/examples/backend/` |
| `P1` | QA domain, Security domain | Team-owned `domain-qa`, `domain-security` repos |
| `P2` | Frontend, DevOps | Team-owned repos |
| `P3+` | All others | Team-owned repos |

Domain teams scaffold their repos using the authoring kit and are not blocked by SDK releases.

## 9. Quality Loop Patterns

Quality is enforced through five stacking loop patterns, from inner agent execution to whole-workflow evaluation.

### 9.1 Loop Overview

| Loop | Level | What It Does | Key Controls |
|---|---|---|---|
| **Loop 1: Execution** | Inside agent | Think → Act → Observe → Repeat | `max_react_steps`, `max_turns`, `timeout`, `budget` |
| **Loop 2: Self-Critique** | Inside agent | GradingEngine scores output → if below threshold → retry with critique | `reflection.max_rounds`, `reflection.threshold`, `grading_criteria` |
| **Loop 3: Gate Retry** | Between stages | Gate checks output → FAIL → retry/abort/escalate/fallback/degrade | `gate.on_fail`, `gate.max_retries` |
| **Loop 4: Cross-Stage Feedback** | Between stages | Downstream agent sends structured feedback → upstream agent fixes | `feedback.to`, `feedback.max_rounds` |
| **Loop 5: Evaluator Loop** | Whole workflow | Planner → workflow → Evaluator grades → revise and re-run | `evaluator_loop.max_rounds`, `evaluator_loop.threshold` |

### 9.2 Loop 1: Execution Loop (Inner)

The think-act-observe cycle inside every agent run. Controlled by `max_react_steps`, `max_turns`, `timeout`, and `budget`.

### 9.3 Loop 2: Self-Critique / Reflexion

After the inner loop finishes, `GradingEngine` scores the output against `grading_criteria.yaml`. If below threshold, the inner loop re-runs with the critique injected as feedback context.

Grading criteria can be **automated** (run a tool and check exit code) or **llm_judge** (ask an LLM evaluator to assess). Each criterion has a weight and a `required` flag (hard gate vs soft score contribution).

```yaml
# grading_criteria.yaml
criteria:
  - id: tests_pass
    type: automated
    check: "All generated tests must pass"
    tool: tool://shared/pytest
    weight: 0.3
    required: true
  - id: lint_clean
    type: automated
    check: "No linting errors"
    tool: tool://shared/ruff
    weight: 0.2
    required: false
  - id: follows_patterns
    type: llm_judge
    check: "Code follows existing architecture patterns"
    weight: 0.2
    required: false
threshold: 0.8
```

### 9.4 Loop 3: Gate Retry

After an agent completes, the workflow gate checks output quality. On failure, the gate action determines behavior:

| Action | Effect |
|---|---|
| `retry` | Re-run the same agent with gate failure as feedback context |
| `abort` | Stop the workflow |
| `escalate_human` | Pause for human review |
| `fallback(agent_ref)` | Try an alternate agent |
| `degrade` | Skip this step and continue |

### 9.5 Loop 4: Cross-Stage Feedback

Downstream agents can send structured feedback to earlier stages. Example: TestAgent finds bugs → sends failure report → CodeGenAgent re-runs with original task + its previous output + test feedback.

Controlled by `feedback.to` (target step), `feedback.max_rounds` (prevent infinite loops), `feedback.format` (structured report schema).

### 9.6 Loop 5: Evaluator Loop (Workflow-Level)

The outermost loop wraps the entire workflow: PlannerAgent defines acceptance criteria → workflow runs → EvaluatorAgent grades against FeatureContracts → if below threshold, PlannerAgent revises and workflow re-runs.

## 10. Complete Control Knobs

### 10.1 Agent-Level Controls (in agent_manifest.yaml)

```yaml
execution_mode:
  primary: react              # react | plan_execute | chain_of_thought | self_ask | tree_of_thought
  planning: plan_execute      # optional planning phase before main strategy
  max_react_steps: 20
  max_turns: 30
  timeout: 300
  reflection:
    enabled: true
    max_rounds: 3
    threshold: 0.8
    criteria_ref: ./grading_criteria.yaml
  thinking:
    enabled: true
    budget_tokens: 8000
  tree_of_thought:
    enabled: false
    branches: 3
    depth: 2
    selection: best_score

budget:
  max_tokens: 50000
  max_cost_usd: 0.50
  max_output_tokens: 8192
  warn_at_percent: 80

constraints:
  allowed_paths: ["src/", "tests/"]
  forbidden_paths: ["config/secrets/", ".env"]
  forbidden_commands: ["rm -rf", "docker run"]
  max_file_changes: 20
  max_lines_changed: 500
  read_only: false
  sandbox: false

permissions:
  file_write: allow           # allow | ask | deny
  shell_execute: allow
  external_api: ask
  git_operations: allow
  dangerous_commands: deny

error_handling:
  retry_on_tool_error: true
  max_tool_retries: 2
  retry_on_llm_error: true
  max_llm_retries: 3
  on_budget_exceeded: stop    # stop | degrade | escalate
  on_timeout: stop
  on_permission_denied: escalate

context:
  sources:
    - type: domain_docs
      path: context/
    - type: codebase
      include: ["src/**/*.py"]
  max_context_tokens: 30000
  priority: relevance
  refresh_between_turns: false

observability:
  trace_level: standard       # minimal | standard | verbose
  log_tool_calls: true
  log_llm_messages: true
  record_for_replay: true
```

### 10.2 Workflow-Level Controls (in workflow YAML)

```yaml
budget:
  max_tokens: 200000
  max_cost_usd: 2.00
  timeout: 1800

evaluator_loop:
  enabled: true
  max_rounds: 3
  threshold: 0.8
  planner_ref: _orchestrator/PlannerAgent/v1
  evaluator_ref: _orchestrator/EvaluatorAgent/v1
  total_budget: 5.00

steps:
  - id: codegen
    agent_ref: backend/CodeGenAgent/v1
    overrides:
      execution_mode:
        max_react_steps: 30
      budget:
        max_cost_usd: 1.00
    gate:
      checks:
        - type: schema_valid
        - type: tests_pass
      on_fail: retry
      max_retries: 3
      retry_with_feedback: true
      escalate_after: 2

  - id: test
    agent_ref: backend/TestAgent/v1
    consumes: [codegen.artifacts]
    feedback:
      to: codegen
      max_rounds: 2
      format: structured
    gate:
      checks:
        - type: tests_pass
      on_fail: abort

reset_points: [after:test]
context_sharing: artifacts_only
```

### 10.3 4-Layer Agent Mode Override

Agent configuration resolves through four layers with deep-merge semantics:

| Layer | Source | Purpose |
|---|---|---|
| 1 | `config/categories.yaml` | Category defaults (model, temperature, base mode) |
| 2 | `agent_manifest.yaml` | Agent author overrides for this specific agent |
| 3 | Workflow step `overrides` | Workflow author adjusts for this workflow context |
| 4 | Runtime CLI/API overrides | Operator adjusts at run time |

Resolution: Layer 1 → Layer 2 → Layer 3 → Layer 4. Each layer deep-merges into the previous.

### 10.4 Controllable Dimensions Summary

| Dimension | Where Defined | What It Controls |
|---|---|---|
| Execution strategy | `execution_mode.primary` | ReAct, PlanExecute, CoT, SelfAsk, ToT |
| Planning phase | `execution_mode.planning` | Optional plan before main strategy |
| Self-critique | `execution_mode.reflection` | Reflexion loop with grading |
| Thinking depth | `execution_mode.thinking` | Extended thinking budget |
| Inner loop limits | `max_react_steps`, `max_turns`, `timeout` | How many iterations allowed |
| Quality criteria | `grading_criteria.yaml` | What gets evaluated and how |
| Gate behavior | `gate.on_fail` | retry, abort, escalate, fallback, degrade |
| Cross-stage feedback | `feedback.to` | Backward loops between stages |
| Evaluator loop | `evaluator_loop` | Whole-workflow quality iteration |
| Budget | `budget.*` | Token, cost, time limits at every level |
| Safety | `constraints.*` | Path, command, file change limits |
| Permissions | `permissions.*` | Allow/ask/deny per action type |
| Error handling | `error_handling.*` | Retry, stop, escalate on failures |
| Context | `context.*` | What knowledge the agent sees |
| Observability | `observability.*` | What gets logged and recorded |
| Model | `category` + provider routing | Which LLM runs the agent |
| Temperature | Category config or override | Creativity vs determinism |

## 11. Authoring, Validation, Certification, and Publication

### 11.1 Golden Path Commands

```bash
./ai domain init qa --owner team-quality
./ai pack create qa regression
./ai agent create --domain qa --pack regression --name RegressionAgent --category reasoning
./ai workflow create --domain qa --name regression_cycle --from-pack regression
./ai validate domain qa
./ai certify domain qa
./ai publish domain qa --visibility internal
```

### 11.2 Authoring Components

| Component | Responsibility |
|---|---|
| `scaffolder.py` | Create domain, pack, agent, workflow, docs, and example tool bindings |
| `validator.py` | Validate manifests, schemas, ports, workflow topology, tool availability |
| `certifier.py` | Execute dry-runs, evaluator thresholds, cloud checks, observability checks |
| `compatibility.py` | Enforce schema/version/port compatibility |
| `publisher.py` | Publish certified domains/workflows/tool packs into catalog |

### 11.3 Certification Gates

A domain may only be published if it passes:
- manifest validity
- schema and port compatibility
- permission and tool safety
- local workflow dry-run
- cloud workflow dry-run
- evaluator threshold
- artifact/logging/trace compliance
- communication compliance (typed contracts only)

## 12. Progressive Autonomy Model

Each domain declares one autonomy profile:

| Profile | Meaning |
|---|---|
| `assisted` | risky actions require human intervention |
| `guarded_autonomous` | non-interactive execution allowed within bounded policies |
| `workflow_autonomous` | cross-workflow chaining and external actions allowed; policy violations escalate |

Autonomy is not global. It is enforced through domain manifest policy plus workflow policy and runtime policy.

## 13. Provider and Execution Strategy

### 13.1 Provider Routing

The provider layer routes by:
- category
- required capabilities
- fallback policy
- cost/budget constraints

Initial provider plan:
- Anthropic first for MVP
- OpenAI and Bedrock in the platform phase
- Gemini later

Representative category-based routing configuration:

```yaml
categories:
  reasoning:
    provider: anthropic
    model: claude-sonnet-4
    temperature: 0.3
    thinking: { enabled: true, budget_tokens: 8000 }

  fast-codegen:
    provider: openai
    model: gpt-4o
    temperature: 0.2
    max_tokens: 8192

  security-analysis:
    provider: anthropic
    model: claude-sonnet-4
    temperature: 0.1

  cost-optimized:
    provider: bedrock
    model: anthropic.claude-3-haiku
    temperature: 0.2
```

Agents may override category defaults when justified by quality or cost trade-offs.

### 13.2 Reasoning / Harness Patterns

Agents or workflows declare execution patterns through structured config rather than bespoke prompt inventions.

#### Available Modes

| Mode | How It Works | Best For | Relative Cost |
|---|---|---|---|
| `react` | Think -> act -> observe -> iterate | Tool-heavy iterative work | Medium |
| `plan_execute` | Plan first, then execute steps | Structured artifact creation | Low-Medium |
| `chain_of_thought` | Deep single-pass reasoning | Review and analysis | Low |
| `self_ask` | Decompose into sub-questions | Systematic analysis | Medium |
| `tree_of_thought` | Explore multiple branches | Critical design decisions | High |
| `reflexion` | Execute, critique, retry | Evaluator-loop quality improvement | High |

#### Stage-Based Default Mapping

| Stage Type | Default Mode | Typical Agents |
|---|---|---|
| Planning | `plan_execute` | `RequirementsAgent`, `DesignAgent` |
| Design trade-off | `plan_execute + tree_of_thought` | `ArchitectureAgent` |
| Generation | `plan_execute + react` | `ScaffoldAgent`, `CodeGenAgent` |
| Analysis / review | `chain_of_thought + self_ask` | `ReviewAgent`, `EvaluatorAgent` |
| Testing | `react` | `TestAgent`, `IntegrationTestAgent` |
| Security | `self_ask + react` | `SecurityScanAgent`, `SASTAgent` |
| Documentation | `plan_execute` | `DocsAgent`, `APIDocAgent` |
| Deployment | `react` | `DeployConfigAgent` |

#### Composite Modes

- **Plan-then-ReAct** for implementation agents that need a short upfront plan followed by tool-guided execution
- **Chain-of-Thought plus Self-Ask** for analysis agents that need both broad judgment and checklist-like decomposition
- **ReAct-with-Reflexion** for evaluator-loop quality improvement
- **Plan-Execute-with-ToT** for high-stakes architectural decision points

#### Execution Mode Configuration

```yaml
execution_mode:
  primary: react
  planning: plan_execute
  reflection: true
  max_react_steps: 20
  max_reflection_rounds: 3
  tree_of_thought: false
```

Runtime overrides are supported at both the agent-run and workflow-run levels so operators can simplify or intensify reasoning without changing the agent implementation.

## 14. Observability, Persistence, and Cost Control

### 14.1 Observability

Every run emits:
- `trace_id`
- `task_id`
- `workflow_id`
- `agent_id`
- `version`
- provider/tool usage
- duration, status, and failure reason

Artifacts may include:
- diff summaries
- test reports
- screenshots
- UI snapshots
- tool outputs
- checkpoints
- evaluator results

### 14.1a Replay, Comparison, and Failure Analysis

Beyond basic logging, the platform should support:
- replay of representative runs using recorded or mocked model outputs
- comparison of evaluator scores across agent versions and execution modes
- failure-focused debugging for workflow topology, tool resolution, or permission denials

### 14.2 Persistence

The platform uses a `StorageBackend` seam.
- MVP: local filesystem
- Later: object storage / database-backed storage

Persistence is required for:
- checkpoints
- workflow resumption
- audit artifacts
- certification evidence
- compatibility metadata

### 14.3 Budget Control

Budgets may be enforced at:
- agent level
- workflow level
- domain policy level
- runtime environment level

Budget policies must be able to stop, downgrade, or escalate execution.

## 15. Testing Strategy

The implementation uses TDD and layered verification.

### 15.1 Test Layers

- **Unit tests** for manifest parsers, adapters, validators, router logic, guardrails, and tool normalization
- **Integration tests** for workflow execution, cross-domain composition, tool resolution, runtime policy, and artifact persistence
- **E2E tests** for `backend/quick_change`, certification flow, and at least one cross-domain workflow
- **Replay tests** for deterministic LLM behavior using recorded/mock responses

### 15.2 Required Verification for Platform Readiness

- manifest schema tests
- compatibility tests
- permission tests
- workflow topology tests
- cloud runtime headless tests
- tool adapter tests for CLI, MCP, and HTTP/API classes
- publication and certification tests

## 16. Tiered Implementation Mapping

All phases are scoped to the framework SDK and shared packages. Domain building is done by teams in their own repos.

### Tier 1 — MVP: Working Single-Domain Loop

#### Phase 0: Foundation (shadow-gentcore)
- repository structure, `AGENTS.md`, docs knowledge tree
- developer Makefile, CLI shell
- record/replay testing infrastructure

#### Phase 1: Contracts Package (agent-contracts)
- All manifest types: `DomainManifest`, `AgentManifest`, `CapabilityPackManifest`, `WorkflowDefinition`, `ToolManifest`, `ToolPackManifest`
- All runtime contracts: `TaskEnvelope`, `RunRecord`, `ArtifactRecord`, `Checkpoint`, `FeatureContract`, `PortBinding`
- Enums: `Category`, `ExecutionMode`
- `StorageBackend` seam

#### Phase 2: Core Harness Engine (shadow-gentcore)
- `prompt_assembler.py`, `mode_dispatcher.py`, `tool_executor.py`
- `agent_runner.py` with full turn loop
- `grading_engine.py`, `budget_tracker.py`
- `provider_router.py` with Anthropic seam only
- `context_engine.py`, `guardrails.py`, `permissions.py`
- `worktree_manager.py`, `handoff.py`, `message_bus.py`
- `composition_engine.py`, `validation_pipeline.py`
- `browser_bridge.py`, `observability_bridge.py`
- `architecture_lints/`

#### Phase 3: Example Backend Domain (shadow-gentcore/examples/)
- 4-agent backend MVP chain (CodeGen, Validate, Test, Review)
- `quick_change` workflow with gates and feedback loops
- durable artifacts and local E2E validation

### Tier 2 — Platform: Evaluator + Multi-Provider + Authoring Kit

#### Phase 4: Evaluator Loop + Quality Loops
- `evaluator_loop.py` (workflow-level Planner → Generator → Evaluator)
- Self-critique / reflexion integration in `agent_runner.py`
- Gate retry, cross-stage feedback in `composition_engine.py`
- `PlannerAgent` and `EvaluatorAgent` platform agents

#### Phase 5: Multi-Provider + Authoring Kit
- OpenAI adapter, Bedrock adapter
- category/capability/fallback routing
- domain/pack/agent/workflow scaffolders
- validator, certifier, compatibility registry, publisher
- SDK packaging (pip-installable `shadow-gentcore`)

#### Phase 6: Shared Tool Library (agent-tools)
- CLI, MCP, HTTP/API adapters
- Language-specific tool packs (Python, Java, Go)
- Tool resolver for `tool://` and `toolpack://` URIs

### Tier 3 — Scale: DAG Execution + Cloud Runtime

#### Phase 7: DAG Execution + Cloud Runtime
- `WorkflowExecutor` sequence/DAG execution with parallel steps
- `CloudRuntime` with fail-closed permission policy
- workflow-defined reset points
- background maintenance workflows
- cross-domain workflow support

## 17. Requirements Coverage Matrix

| Design Area | Requirements Covered |
|---|---|
| Multi-repo architecture and taxonomy | R1, R2, R15 |
| Typed contracts and port model | R3, R5 |
| Domain isolation and policies | R4, R7, R8 |
| Runtime parity and execution engine | R5, R6, R9, R18, NFR1 |
| Tool and integration model | R8, R10, R16, NFR3 |
| Validation/certification/publication | R10, R11 |
| Repository knowledge and mechanical enforcement | R17, NFR4 |
| Observability and storage | R12, R17, NFR1, NFR4 |
| Budgets and routing | R13 |
| Maintenance workflows | R14 |
| Adoption scalability | R15 |
| Agent = Configuration, Not Code | R1, R2, R15 |
| Quality loop patterns | R9, R10, R14 |
| Complete control knobs | R7, R8, R13 |
| Multi-language support | R16 |

## 18. Key Decisions

- Use `workflow` as the product term and reserve `pipeline` for low-level executor discussion only.
- Use repo-local manifests and documents as the source of truth.
- Keep tool integration declarative through manifests and adapters.
- Prefer workflow-level composition over raw agent chaining across domains.
- Require certification before publication.
- Allow tasks and workflows to mature through progressive autonomy instead of requiring immediate full autonomy.
- Keep orchestrator and maintenance concerns in platform-owned namespaces rather than inside business domains.
- Treat execution modes as configuration with stage-based defaults and runtime override, not as hardcoded agent behavior.
- Use multi-repo architecture with `agent-contracts`, `shadow-gentcore`, `agent-tools`, and `domain-*` repos.
- Agent = Configuration, Not Code: one AgentRunner class, agents differ only by manifest bundle.
- Agents are language-agnostic — the harness is Python, but agents target any language via tool packs.
- PromptAssembler is the key mechanism that turns a generic runner into a purpose-specific agent.
- Quality is enforced through 5 stacking loop patterns, not a single pass.
- Configuration resolves through 4 layers: category → manifest → workflow → runtime.

## 19. Out of Scope for This Spec

- Building domain-specific agents in the SDK repo (teams own their `domain-*` repos)
- Full provider parity across every possible model vendor in the MVP
- Building every planned domain before proving the backend MVP and authoring kit
- Unbounded agent autonomy without certification, observability, and policy controls
- Agent-to-agent streaming within workflows (deferred)
- A/B testing of agent versions (deferred)
- Cost forecasting before workflow execution (deferred)
