# End-to-End User Guide — shadow-gentcore

Full user journey: from a fresh project repo → genesis-built domain agents → real task execution with self-healing workflows.

All commands in this guide have been exercised against a live Minimax m2.7 endpoint at `https://api.minimax.io/anthropic`.

---

## 0. The four repos in the agent framework

| Repo | Role | What it contains |
|------|------|------------------|
| [`agent-contracts`](../../agent-contracts) | Shared Pydantic types | `DomainManifest`, `AgentManifest`, `WorkflowDefinition` |
| [`shadow-gentcore`](./) | SDK + CLI | `harness/`, `ai` CLI, genesis agents, shared stage agents |
| [`agent-tools`](../../agent-tools) | Tool packs | FastAPI, React, GitHub, Jira, Slack tool manifests |
| [`gentcore-template`](../../gentcore-template) | Domain starter template | Empty `domain.yaml`, empty `agents/`, empty `workflows/` |

A **domain repo** (e.g. `acme-backend`) is your product repo with one extra file: `domain.yaml`. Everything else — agents, workflows, context docs — is generated.

---

## 1. Create a domain repo from the template

The template repo (`gentcore-template`) is the seed. For a new product team:

```bash
# Option A — copy the template
cp -r /path/to/gentcore-template /path/to/my-backend

# Option B — your existing repo just needs a domain.yaml
cd /path/to/my-backend
cp /path/to/gentcore-template/domain.yaml .
```

Edit `domain.yaml`:

```yaml
name: my-backend
version: "1.0.0"
description: "What this service does"
purpose: "Business intent — why this domain exists"
owner: backend-team
industry: ecommerce            # or fintech, healthcare, aws-ops, k8s-ops...

provider:
  name: anthropic
  model: claude-sonnet-4-5-20250929
  max_tokens: 8192
  api_key_env: ANTHROPIC_API_KEY

capabilities:                  # hints for genesis
  - fastapi
  - sqlalchemy
  - pytest

workspace_policy:
  file_edit: allow
  shell_command: ask
  require_tests: true
```

Register your domain in `shadow-gentcore/config/workspace.yaml`:

```yaml
teams:
  my-backend:
    industry: ecommerce
    trusted: true
    reference:
      - path: ../my-backend/src
    target:
      - path: ../my-backend/src
      - path: ../my-backend/tests
    docs:
      - path: ../my-backend/docs
      - path: ../my-backend/context
```

**At this point** your domain repo has `domain.yaml` but no agents yet.

---

## 2. Run genesis → real domain agents from your codebase

Genesis is an **11-step pipeline** that **reads your repo, docs, and capabilities**, then writes purpose-built agents into your domain repo. The 11-step
shape is the result of the **Genesis Complexity Upgrade** plus the
**Best-Practice Overlay (Tier 1.5)** — no new required config keys were
introduced; the pipeline simply got smarter about handling messy real-world
inputs (many reference repos, versioned docs, ambiguous ownership) and
catches missing-but-recommended industry standards via the new `advise` step.

### Dry-run first (zero API cost)

```bash
cd /path/to/shadow-gentcore
./ai genesis scan --team my-backend --dry-run
./ai genesis build --team my-backend --dry-run
```

The pipeline runs:

```
scan → map → resolve → (discover_tools ∥ engineer_context) → verify
     → advise → synthesize_tools → architect → build → validate
```

- **SourceScannerAgent** — walks `reference/` + `target/` + `docs/`, builds an inventory. Every extracted standard now carries an `evidence` trail (file, line range, quote) — templated / made-up rules fail the schema.
- **KnowledgeMapperAgent** — classifies findings and emits a `coverage` score per category plus overall. The `map_gate` blocks the pipeline when coverage is too thin.
- **ConflictResolverAgent** — when multiple reference repos disagree or docs have version suffixes (`v1`, `-old`, `-deprecated`, …), this step arbitrates using a fixed 6-rung tiebreaker ladder. Emits `resolved_knowledge_map` with `source_attribution` on every rule and a `contested_items[]` list. **Zero new config required** — the agent infers priorities from the repos and docs themselves.
- **ToolDiscoveryAgent** — picks tool packs matching your stack (FastAPI → `toolpack://core/fastapi`).
- **ContextEngineerAgent** — generates `context/standards.md` and `context/architecture.md` from the resolved knowledge map.
- **ContextVerifierAgent** — re-reads a sample of cited sources with `file_read` and scores how well the generated docs are grounded in real files (`grounding_score`). Low grounding loops back to `ContextEngineer` with the specific unsupported claims rather than regenerating the whole document.
- **BestPracticeAdvisorAgent** (NEW, the `advise` step) — diffs the generated `standards.md` against the curated industry library (`config/best_practices/<industry>.yaml`) and writes a `context/best_practices.md` Tier 1.5 overlay flagging missing-but-recommended principles. The overlay is auto-injected into every generated agent's prompt at runtime via the `best_practices_overlay` preload source. See [BEST_PRACTICES_OVERLAY.md](BEST_PRACTICES_OVERLAY.md).
- **ToolSynthesizerAgent** — closes tool gaps by wrapping public MCP servers or synthesising new tool packs, subject to a static security scan.
- **AgentArchitectAgent/v2** (catalog-driven, default) — designs the agent roster + workflow graph with proper **gates + feedback loops**. Coverage-aware gate now requires ≥ 2 roster entries and a valid DAG.
- **AgentBuilderAgent** — writes every file: `agents/<Name>/v1/{agent_manifest.yaml, system_prompt.md, grading_criteria.yaml}` and `workflows/*.yaml`. Sets `persist_files: true` on code-writing agents so their generated files reach disk at run time. A post-write normalizer repairs common LLM schema drifts (list-shaped constraints, invented preload names, list-shaped workflow refs). Gate requires ≥ 3 files actually written (not just planned).
- **QualityGateAgent** — validates the generated artifacts. `validation_passed` must be `true`.

### When to use which Genesis workflow

| Situation | Command |
|-----------|---------|
| Single coherent target + ≤ few reference repos | `./ai genesis build --team <name>` (runs `genesis_build.yaml`) |
| One team pointed at many repos across different stacks | `./ai genesis plan --team <name>` first (runs `genesis_org_plan.yaml`) — emits `domain_plan[]`, then run `genesis build` once per suggested domain |
| Multiple domains already built and have drifted on style | `./ai genesis sync-house-style` (runs `workflows/maintenance/house_style_sync.yaml`) |
| Iterative improvement of a domain post-build | `./ai genesis evolve --team <name>` (runs `genesis_evolve.yaml`, unchanged) |

**Config stays the same** — the existing `teams.<name>` block with
`reference / target / docs / industry / focus / output` covers every scenario.
You never hand-author priority numbers, version flags, or domain boundaries.
If `ConflictResolver` or `DomainPlanner` cannot decide on its own, it emits
`decision: ask-human` and lists the ambiguous items for you to answer as a
question — you never edit yaml to resolve a conflict.

### Real build (needs API key)

```bash
# Standard Anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# Or route through a compatible gateway (e.g. Minimax)
export ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic
export ANTHROPIC_AUTH_TOKEN=sk-cp-...

./ai genesis build --team my-backend
```

---

## 2.5 What genesis produces — you do not author this

**Every file listed below is machine-generated.** Your one required input was `domain.yaml`. Everything else is emitted by the pipeline agents:

```
my-backend/
├── domain.yaml                  # ← YOU wrote this (1 required file)
├── .gentcore/
│   └── genesis-manifest.json    # ← regen-safety hash record — Builder refuses to overwrite hand-edited files unless GENTCORE_FORCE_OVERWRITE=1
├── context/                     # ← generated by ContextEngineerAgent + BestPracticeAdvisorAgent
│   ├── standards.md             #   injected into every domain-agent prompt (Tier 1)
│   ├── best_practices.md        #   ← NEW Tier 1.5 overlay, also auto-injected, flags missing industry standards
│   ├── architecture.md          #   injected on demand
│   ├── glossary.md              #   domain vocabulary
│   └── reference_index.yaml     #   Tier 2 keyword-indexed chunks
├── agents/                      # ← generated by AgentBuilderAgent (catalog-reused or synthesised)
│   ├── CodeWriterAgent/v1/      #   persist_files: true — writes generated code to workspace_root
│   ├── TestRunnerAgent/v1/      #   persist_files: true — writes tests + runs pytest
│   ├── MigrationAgent/v1/       #   persist_files: true — writes Alembic migrations
│   ├── ReviewerAgent/v1/
│   ├── SecurityScanAgent/v1/
│   └── TriageAgent/v1/          # ← classifies incoming task requests
└── workflows/                   # ← generated by AgentBuilderAgent, ONE PER TASK TYPE
    ├── triage.yaml              #   ★ the single /run entry point — dispatches below
    ├── feature_delivery.yaml    #   plan → implement → test → review
    ├── bug_fix.yaml             #   reproduce → fix → verify → review
    ├── refactor.yaml            #   plan → apply → test → review
    └── security_review.yaml     #   audit → triage → fix → review
```

> **Agent name list above is illustrative** — your actual generated set
> depends on what `AgentArchitectAgent/v2` picked from the `_shared/`
> catalog (~70% reuse target for SWE domains). Run `ls agents/` after
> genesis to see your domain's actual roster.

Consumers of the generated service call one endpoint:

```bash
POST /run {"task": "Add /items/{id}/reviews with pagination"}   # → feature_delivery
POST /run {"task": "The /users endpoint 500s on null email"}    # → bug_fix
POST /run {"task": "Refactor auth middleware to async"}         # → refactor
```

The TriageAgent classifies the incoming task and the triage workflow's router gate dispatches to the matching workflow. Consumers never have to know which workflow to call.

### How the design gets decided

1. **AgentArchitectAgent** (v2 is default — catalog-driven since Phase 2d) enumerates the domain's stages and for each one looks up `agents/_shared/` (~20 reusable stage agents: TestRunner, Reviewer, Linter, Migration, SecurityScan, Compliance, etc.). If the catalog has a fitting agent → reuse it (`decision: reuse-core`). Otherwise → mark for synthesis. Aim: ≥70% reuse for SWE domains.
2. Architect emits: agent roster + per-agent `harness` block (gate_condition, gate_on_fail, max_retries, fallback_step, grading_threshold) + workflow_design with gates + feedback_loops + parallel_branches + reset_points.
3. **AgentBuilderAgent** writes every file, transposing the architect's design directly into YAML. No manual authoring anywhere in this pipeline.

### What a generated workflow YAML looks like

This is the exact shape `AgentBuilderAgent` emits — **you do not write this by hand**:

```yaml
# my-backend/workflows/feature_delivery.yaml   ← 100% machine-generated
name: feature_delivery
version: "2.0.0"
description: "Plan → implement → test → review with self-healing feedback loops"

steps:
  - name: plan
    agent: my-backend/APIFeaturePlannerAgent/v1
    gate: {condition: "status == success", on_fail: retry, max_retries: 1}

  - name: implement
    agent: my-backend/FastAPICodeGenAgent/v1
    depends_on: [plan]
    gate: {condition: "status == success", on_fail: retry, max_retries: 2}

  - name: test
    agent: my-backend/PytestRunnerAgent/v1
    depends_on: [implement]
    gate:
      condition: "all_passed == true"
      on_fail: retry
      max_retries: 2
      fallback_step: implement

  - name: review
    agent: my-backend/CodeReviewerAgent/v1
    depends_on: [test]
    gate:
      condition: "approved == true"
      on_fail: retry
      max_retries: 1
      fallback_step: implement

reset_points: [implement]

feedback_loops:
  - from_step: test
    to_step: implement
    condition: "all_passed == false"
    max_iterations: 2
  - from_step: review
    to_step: implement
    condition: "approved == false"
    max_iterations: 2
```

### Controlling what gets generated — `workflows:` override

By **default**, genesis auto-discovers workflow types from the reference repos (git log prefixes like `feat:`/`fix:`/`refactor:`, issue/PR templates, CONTRIBUTING.md, etc.). Each distinct task type becomes one workflow YAML.

To override (fully or partially), add a `workflows:` block to `domain.yaml`:

```yaml
# my-backend/domain.yaml
workflows:
  # A — EXPLICIT (disables auto-discovery entirely)
  processes:
    - feature_delivery
    - bug_fix
    - security_review        # domain-specific, not in any catalog

  # OR B — DELTAS (keep auto-discovery, add/remove)
  add_processes:    [gdpr_erase, data_export]
  exclude_processes: [refactor]        # auto found it but team doesn't want it

  # Optional — explicit triage. Otherwise auto-derived from workflow names.
  triage:
    classifier: "_shared/TriageAgent/v1"
    buckets:  [feature, bug, security]
    routes:
      feature:  feature_delivery
      bug:      bug_fix
      security: security_review
      unknown:  human_review
```

Resolution precedence:

1. `workflows.processes` set → use verbatim (override wins)
2. `add_processes` / `exclude_processes` → apply deltas to auto-discovery
3. Nothing declared → full auto-discovery
4. If auto-discovery produced fewer than 3 processes → fall back to stack defaults (industry-aware — backend gets `[feature_delivery, bug_fix, refactor, docs_refresh]`, ops gets `[incident_triage, runbook_execution, capacity_review, cost_optimization]`, etc.)

After genesis runs, the CLI prints what it chose + confidence + signal sources, so you can see what to override:

```
Genesis resolved 5 workflow processes (4 auto-discovered, 1 user-added):
  - feature_delivery       (0.92  from git log + PR template)
  - bug_fix                (0.88  from git log + issue template)
  - refactor               (0.71  from git log)
  - dep_upgrade            (0.64  from Dependabot)
  - docs_refresh           (0.55  from CONTRIBUTING.md)
  - gdpr_erase             (override  from domain.yaml add_processes)
Override via `workflows:` in domain.yaml.
```

### How to change the design

Do **not** hand-edit `workflows/*.yaml` or `agents/*/v1/*`. Instead, change the upstream inputs and re-run genesis:

- Want different stages? → refine `domain.yaml` `capabilities:` or `context/architecture.md`
- Want tighter gates? → tighten `workspace_policy` or add `compliance_draft` docs
- Want an agent that doesn't exist in `_shared/`? → it gets synthesised automatically; to influence its shape, refine the context docs
- Want a new domain-specific agent on demand? → use the factory (`_factory/AgentFactoryAgent`) — see [FACTORY_VS_BUILDER.md](FACTORY_VS_BUILDER.md)

Then:

```bash
./ai genesis build --team my-backend   # regenerates everything
```

Run a health check:

```bash
./ai test smoke --domain /path/to/my-backend
# Score: 100%, N agents, N workflows healthy.
```

---

## 3. Run your domain agents on real tasks

### Single agent

```bash
./ai run agent my-backend/FastAPICodeGenAgent/v1 \
  --task "Add a GET /items/{item_id}/reviews endpoint with cursor pagination" \
  --domain /path/to/my-backend \
  --dry-run
```

Without `--dry-run`, the agent:
- Reads `domain.yaml` → picks provider + model
- Reads its manifest → execution_mode, tools, permissions
- Reads `context/standards.md` → coding conventions
- Calls the LLM with submit_output forced (H1) → schema-valid JSON
- Calls registered tools (`file_write`, `file_read`, `shell_exec`, `pytest_asyncio`, etc.)

### Workflow (DAG with self-healing)

```bash
./ai run workflow /path/to/my-backend/workflows/feature_delivery.yaml \
  --domain /path/to/my-backend \
  --task '{
    "feature_request": "Add product search by name and price range",
    "branch": "feat/product-search",
    "workspace_root": "/path/to/working/tree"
  }' \
  --dry-run
```

> **Critical for getting files on disk: `workspace_root` + `persist_files`.**
>
> Code-writing agents (CodeWriter, MigrationAgent, TestRunner,
> RefactorPlanner) emit a structured `files: [{path, content}]` array in
> their output. The framework writes those files to disk **only when**:
> 1. The agent's manifest declares `persist_files: true` (the Builder
>    template does this automatically for code-writing agents post-Q2 2026)
> 2. The task input includes a `workspace_root` (or `output_dir`)
>
> Without both, the agent runs and produces schema-valid output, but the
> files die in Tier 4 memory and never reach your filesystem. If you
> upgraded an old domain, check each code-writing agent's manifest for
> `persist_files: true` — re-run genesis with `GENTCORE_FORCE_OVERWRITE=1`
> to regenerate, or hand-set the field per agent.

> **Strict-mode for production:** set `GENTCORE_STRICT_MANIFESTS=1` so
> any manifest schema drift fails fast at load time instead of warning.
> Recommended for CI and HTTP server startup.

The workflow engine handles:

| Mechanism | What it does |
|-----------|--------------|
| **Gate condition** | `all_passed == true` — evaluated against the agent's output |
| **on_fail: retry** | Re-runs the same step with the failure as feedback |
| **max_retries** | Cap on retry attempts (default 2) |
| **fallback_step** | On gate fail, jump back to an upstream step (e.g. `implement`) |
| **feedback_loops** | Named loops from one step to another triggered by output condition |
| **reset_points** | Safe checkpoints the engine can roll back to |
| **retry_with_feedback** | Prior failure context injected into retry's prompt |

Example workflow YAML with self-healing:

```yaml
name: feature_delivery
version: "2.0.0"

steps:
  - name: implement
    agent: my-backend/FastAPICodeGenAgent/v1
    gate: {condition: "status == success", on_fail: retry, max_retries: 2}

  - name: test
    agent: my-backend/PytestRunnerAgent/v1
    depends_on: [implement]
    gate:
      condition: "all_passed == true"
      on_fail: retry
      max_retries: 2
      fallback_step: implement

  - name: review
    agent: my-backend/CodeReviewerAgent/v1
    depends_on: [test]
    gate:
      condition: "approved == true"
      on_fail: retry
      max_retries: 1
      fallback_step: implement

reset_points: [implement]

feedback_loops:
  - name: test_failed
    from_step: test
    to_step: implement
    condition: "all_passed == false"
    max_iterations: 2

  - name: review_rejected
    from_step: review
    to_step: implement
    condition: "approved == false"
    max_iterations: 2
```

---

## 4. The zero-token testing path

For CI or offline verification, use `SmokeTestProvider` which emits schema-correct stubs:

```bash
./ai test smoke                           # full journey: scaffold + genesis + workflow
./ai test smoke --cross-domain            # backend + frontend cross-domain flow
./ai test smoke --domain /path/to/my-backend   # health check only
```

13/13 steps green = framework installation is sane.

---

## 5. Routing through alternate LLM providers

The `AnthropicProvider` accepts either:

```python
# Standard (x-api-key header)
provider = AnthropicProvider(api_key="sk-ant-...", model="claude-sonnet-4-5-20250929")

# Bearer-token auth + alternate endpoint (Minimax, OpenRouter, Bedrock-compat...)
provider = AnthropicProvider(
    auth_token="sk-cp-...",
    base_url="https://api.minimax.io/anthropic",
    model="m2.7",
)

# Or via env vars (picked up automatically)
os.environ["ANTHROPIC_AUTH_TOKEN"] = "..."
os.environ["ANTHROPIC_BASE_URL"] = "https://api.minimax.io/anthropic"
provider = AnthropicProvider(model="m2.7")
```

**Model guidance:**
- Genesis + architect work → **Opus-class** (complex design reasoning)
- Feature implementation → **Sonnet-class** or **m2.7** (good for codegen)
- Simple reviews / gates → **Haiku-class** (fast, cheap)

Mixed-model workflows: set `provider.model` per step in `agent_manifest.yaml`.

---

## 6. Example trace — what a successful run looks like

Against a real LLM, a clean run emits this in `execution_log`:

```
[step_started    ] plan
[gate_passed     ] plan
[step_completed  ] plan       duration: 10s, tokens: 1200

[step_started    ] implement
[gate_passed     ] implement
[step_completed  ] implement  duration: 65s, tokens: 8400, files_written: 11

[step_started    ] test
[gate_passed     ] test       (all_passed == true)
[step_completed  ] test       duration: 22s, tokens: 2100

[step_started    ] review
[gate_passed     ] review     (approved == true)
[step_completed  ] review     duration: 15s, tokens: 1800

overall status: completed
```

If any step regresses, you see the recovery path fire:

```
[step_started    ] test
[gate_failed     ] test       action: retry
[gate_retry      ]            attempt 1, strategy: retry_with_feedback
[feedback_loop_triggered] test → implement (iteration 1)
[step_started    ] implement  (re-running with prior failure context)
[step_completed  ] implement
[step_started    ] test
[gate_passed     ] test
overall status: completed
```

---

## 7. Troubleshooting — common failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `output_validation_failed: Missing required field` | LLM emitted prose, not structured JSON | Ensure `output_schema` in manifest lists required fields; use a stronger model |
| `Gate failed for test` | `all_passed == true` was false | Correct — the gate caught broken code. Fix the underlying implementation (or retry cap was reached) |
| `retry_exhausted` | Retries didn't converge | Increase `max_retries`, switch to stronger model, or widen `reset_points` |
| `tool 'file_write' not registered` | Tool pack wasn't loaded | Check `agent_tools` is installed; `register_builtins` is called by `ManifestLoader.boot_engine` |
| `unhashable type: 'slice'` | Legacy slice on promoted dict | Fixed in composition_engine.py — `str(output)[:N]` coerces to string |

---

## 8. Smoke testing commands (no API required)

All these work with `SmokeTestProvider`:

```bash
# Full scaffolded journey
./ai test smoke

# Cross-domain (backend + frontend in parallel)
./ai test smoke --cross-domain

# Genesis on acme-backend (example domain)
.venv/bin/python scripts/smoke_genesis_acme.py
.venv/bin/python scripts/smoke_run_feature.py

# Real LLM round-trip (requires env vars)
.venv/bin/python scripts/minimax_real_agent.py       # single agent
.venv/bin/python scripts/minimax_real_workflow_writes.py  # full DAG with file writes
```

---

## 9. What changed in this release

- **AgentArchitectAgent/v2 is now the default.** Set `GENTCORE_ARCHITECT_V2=0` to force v1.
- **Agent output fields are promoted to top-level** in both `agent_runner.py` and `composition_engine.py` so gate expressions like `all_passed == true` work against schema-declared fields (previously only `status == success` worked because output was always serialized to a string).
- **Provider supports alternate endpoints** via `base_url` + `auth_token` constructor args, or `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` env vars.
- **Scaffolded agents support `shell_exec`** — validators can run real `py_compile` and `pytest` instead of hallucinating test results.

---

## Appendix A — Why are the genesis agents pre-built?

A natural follow-up question: "The genesis agents are also just YAML manifests + prompts. Are they generated by the framework too?"

**Short answer: no. They're bootstrap.** The 15 agents in `shadow-gentcore/agents/_genesis/` (SourceScannerAgent, KnowledgeMapperAgent, ConflictResolverAgent, ToolDiscoveryAgent, ContextEngineerAgent, ContextVerifierAgent, BestPracticeResearchAgent, BestPracticeAdvisorAgent, ToolSynthesizerAgent, AgentArchitectAgent, AgentBuilderAgent, QualityGateAgent, DomainPlannerAgent, HouseStyleAgent, EvolutionAgent) ship pre-built with the SDK because they have to exist before any domain can be created — a chicken-and-egg problem.

**But they CAN be improved by the framework itself.** Two paths:

- **`_factory/AgentFactoryAgent/v1/`** — given a `CapabilityRecipe` ({goal, tools, context}), synthesises ONE new agent on demand. Called when a genesis run detects a coverage gap in the `_shared/` catalog. Triggered from `workflows/factory/learn_and_create.yaml`.

- **`_genesis/EvolutionAgent/v1/`** — takes production `RunRecord` data and refines existing agents: tighter prompts, better grading criteria, new example shots. Triggered from `workflows/genesis/genesis_evolve.yaml` on a schedule or after a review cycle.

This is the self-improving loop the design promises. It is **opt-in, not the default path** — the framework doesn't auto-evolve its own agents on every domain build because that would conflate two kinds of change (domain design vs. framework improvement). To opt in, run:

```bash
./ai genesis evolve --team my-backend   # refine existing agents from run records
```

See [FACTORY_VS_BUILDER.md](FACTORY_VS_BUILDER.md) for the detailed split.

---

## Appendix B — The catalog of reusable stage agents

`agents/_shared/` contains ~20 stage agents that `AgentArchitectAgent/v2` picks from before synthesising anything new. If one of these fits your domain's stage, you inherit it rather than getting a freshly-synthesised variant:

| Category | Agents |
|----------|--------|
| **Analyze** | SpecAnalyzerAgent, RefactorPlannerAgent, DependencyAnalyzerAgent |
| **Generate** | CodeWriterAgent, DocGeneratorAgent, MigrationAgent, ChangelogAgent |
| **Review** | ReviewerAgent, ComplianceCheckerAgent |
| **Execute / Validate** | TestRunnerAgent, IntegrationTestAgent, PerformanceTestAgent, LinterAgent, SecurityScanAgent, EnvironmentValidatorAgent |
| **Deploy / Operate** | DeployAgent, RollbackAgent |
| **Respond / Summarize** | NotifierAgent, TicketAgent, ReportAggregatorAgent |

Reuse is tracked explicitly in the architect's output as `decision: reuse-core` vs. `decision: synthesize-new` per roster entry, with a `reuse_ratio` field in the design_quality summary.

**Known catalog gaps (future additions):** RetrievalAgent (RAG), TriageAgent, ObservabilityAgent. Until these land, workflows that need them use `_factory/AgentFactoryAgent` to synthesise on demand.

---

## 10. Where to look next

- **Full system architecture:** [docs/SYSTEM_GUIDE.md](./SYSTEM_GUIDE.md)
- **Team operational guide:** [docs/TEAM_GUIDE.md](./TEAM_GUIDE.md)
- **Audit findings + Phase 2 fixes:** [docs/FRAMEWORK_AUDIT_2026Q2.md](./FRAMEWORK_AUDIT_2026Q2.md)
- **Factory vs Builder:** [docs/FACTORY_VS_BUILDER.md](./FACTORY_VS_BUILDER.md)
- **Genesis agent prompts (canonical):** `agents/_genesis/*/v*/system_prompt.md`
- **Shared stage agents (reusable):** `agents/_shared/*/v*/`
