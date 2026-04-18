# Framework Audit — 2026 Q2

**Scope:** `agent-contracts`, `shadow-gentcore`, `agent-tools`, `gentcore-template`, `acme-backend`
**Phase:** 1 of 2 (review-only; Phase 2 implements the approved fix list)
**Status:** ready for approval
**Companion plan:** `.windsurf/plans/agent-framework-audit-fee0b6.md`

---

## 0. Executive summary

| Area | Health | One-liner |
|------|--------|-----------|
| Auto-learn pipeline (scan → map → context) | 🟢 strong | Best subsystem; preserve untouched |
| Agent concept (thin-agent + tools) | 🟢 sound | Core right; 20 stages are ~55% SWE-biased, need 6 generic additions |
| Tool ecosystem | 🟡 solid foundation | 42 packs including AWS/CloudWatch/PagerDuty/Slack; no capability metadata, MCP registry nearly empty |
| Genesis intelligence | 🟡 functional, improvable | Architect prompt/schema drift; `industry` input dead; `reset_points` dead; Factory vs Builder overlap |
| Harness & output consistency | 🟠 inconsistent | Dual condition DSLs; priority field dead; `submit_output` dormant |
| **Permission / rule engine** | 🔴 **dead wiring** | 6-layer model advertised; only 1 layer (platform+trusted) actually runs. `set_rule_context` defined but never called in production |
| Observability of runs | 🟠 minimal | Execution log exists; no per-step token/cost/retry counts surfaced |
| Stage + Tools readiness for complex domains | **~45%** | Path to 90%+ is additive; no rewrite needed |

**Highest-impact targets** (by _impact × confidence / effort_):

1. 🔴 **H3** — wire `set_rule_context` into `AgentRunner` → `ToolExecutor` (permission engine is currently near-noop beyond platform floor)
2. 🟠 **H2** — unify `CompositionEngine._evaluate_condition` and `OutputValidator._eval_check` into one AST-based evaluator
3. 🟢 **B1** — add 6 generic stage agents (`Triage, Investigate, Execute, Respond, Summarize, Retrieve`)
4. 🟢 **B2** — capability → toolpack map + stage default capabilities
5. 🟢 **B5** — `AgentArchitect` prompt/output_schema upgrade (catalog-driven composition; add `capability_bindings`)
6. 🟢 **B9** — AWS-ops thin pilot (zero new agents; proves B1+B2 end-to-end)
7. 🟡 **H1** — always-on `submit_output` final-summary call
8. 🟡 **G1/S1** — `Stage` enum + tag all 20 `_shared/` agents

Remaining fixes covered in §8.

---

## 1. Inventory

### Repositories

| Repo | Role | Key notes |
|------|------|-----------|
| `agent-contracts` | Shared Pydantic contracts | `AgentManifest`, `WorkflowDefinition`, `TaskEnvelope`, `FeatureContract`, `ExecutionMode`, `Category` |
| `shadow-gentcore` | Engine + 37 built-in agents + workflows + configs | 8 genesis + 20 shared + 4 factory + 3 maintenance + 2 orchestrator |
| `agent-tools` | Tool adapters + 42 packs | 4 adapter classes (`cli`, `http_api`, `mcp`, `python`); `core/ cloud/ observability/ services/ analysis/` + domain packs |
| `gentcore-template` | Empty starter scaffold | `domain.yaml`, bootstrap scripts, CI workflows |
| `acme-backend` | Reference domain | 4 generated agents; FastAPI-ish; demonstrates integration |

### Tool pack coverage (abbreviated)

- `core/` — filesystem, search, shell, git, http (~6 packs)
- `cloud/` — aws, aws_advanced, kubectl, terraform (4 packs)
- `observability/` — cloudwatch, datadog, pagerduty (3 packs)
- `services/` — slack, github, github_workflow, jira, confluence (5 packs)
- `analysis/` — snyk, sonarqube, codecov (3 packs)
- Domain-specific — fastapi, react, python_build, docs, frontend, pm, … (~21 packs)

### MCP registry

- `@/Users/yiminguo/shadow-gentcore/config/mcp_servers.yaml:13-23` — only `context7` currently enabled; github/puppeteer/filesystem commented templates. No ops-oriented MCP servers (aws, k8s, datadog, sentry, opsgenie, grafana) wired.

### Tests

- ~862 tests currently green (baseline per plan). Key dirs: `harness/tests/`, golden at `tests/golden/test_it_backend/`.

---

## 2. 1A — Genesis intelligence deep dive

### Pipeline (`@/Users/yiminguo/shadow-gentcore/workflows/genesis/genesis_build.yaml:1-127`)

```
scan (SourceScanner)
  → map (KnowledgeMapper)
    → { discover_tools (ToolDiscovery) ∥ engineer_context (ContextEngineer) }
      → architect (AgentArchitect)
        → build (AgentBuilder)
          → validate (QualityGate)
```

Feedback loops: `validate → build` (max 3), `validate → engineer_context` (max 1).
Reset points: `[map, architect, build]` — **declared but unused** (finding G-RST).
Budget: 500K tokens / $25 / 1h.

### Per-agent assessment

| Agent | Verdict | Rationale |
|-------|---------|-----------|
| `SourceScannerAgent` | 🟢 keep | Tri-modal scanning (reference deep / target moderate / docs light); 100 files / 15 steps budget is realistic for typical repos |
| `KnowledgeMapperAgent` | 🟢 keep | Clean taxonomy (`patterns / conventions / tools / workflow_processes`); coverage scoring is actionable |
| `ToolDiscoveryAgent` | 🟡 tweak | `search_files` across 42 packs every run; no pack capability tags — agent has to LLM-read descriptions and guess. Fix via B3 (tag packs with `provides`) |
| `ContextEngineerAgent` | 🟢 keep, + extend | Solid `standards.md` + `architecture.md` + `reference/*.md`. Add optional `runbooks/` section when runbook docs detected (B7) |
| `AgentArchitectAgent` | 🟠 rework prompt | See **G-ARC** — invents rosters instead of composing from catalog; `industry` input dead; output_schema drift |
| `AgentBuilderAgent` | 🟡 tweak | See **G-BLD** — "targeted retry" rule is unenforced |
| `QualityGateAgent` | 🟡 tweak | See **G-QGT** — shells out to `./ai validate` when in-process `verify_genesis_output()` exists |
| `EvolutionAgent` | 🟢 keep | Advisory-only today; auto-apply path out of scope |

### Key genesis findings

**G-ARC: `AgentArchitect` prompt/schema drift + invented rosters.**

- `@/Users/yiminguo/shadow-gentcore/agents/_genesis/AgentArchitectAgent/v1/system_prompt.md:121-130` — prompt lists ~15 fields each agent MUST have (including `harness`).
- `@/Users/yiminguo/shadow-gentcore/agents/_genesis/AgentArchitectAgent/v1/agent_manifest.yaml:55-103` — output_schema `required` per agent_roster item is `[name, purpose, category, version, description, execution_mode, tools, permissions, constraints, input_schema, output_schema]`. **`harness` is NOT required.** Validator accepts designs without it; Builder then has nothing to wire gates from.
- Prompt line 138 says "Reference existing shared agents. Read `agents/_shared/` to see how well-formed agents look" but gives no catalog. Architect re-invents rather than composes. **This is the root cause the user flagged: non-SWE domains get bespoke agents synthesised each run instead of reusing generic stages.**
- `input_schema.industry` (lines 43-45) declared but never referenced in the prompt → dead parameter until B4/B5.

**Fix (B5):** rewrite prompt to be catalog-driven; add `capability_bindings` per step to output_schema; enforce `harness` as required. Ship behind `--architect-v2` feature flag to preserve parity with current output.

---

**G-BLD: Builder "targeted retry" is unenforced.**

- `@/Users/yiminguo/shadow-gentcore/agents/_genesis/AgentBuilderAgent/v1/system_prompt.md:142` — "only regenerate the specific files mentioned in the feedback, not the entire domain. This saves tokens and preserves working files."
- `@/Users/yiminguo/shadow-gentcore/workflows/genesis/genesis_build.yaml:88-91` — feedback format injects `{validate.targeted_feedback}` but no code restricts `file_write` to that list.
- Risk: Builder can burn tokens re-writing healthy files after a small QualityGate failure.

**Fix (G6):** pass `targeted_files: [...]` into the step config; fail-closed in Builder's prompt-assembler if write path is outside the allowlist.

---

**G-QGT: QualityGate shells out instead of calling the in-process verifier.**

- `@/Users/yiminguo/shadow-gentcore/agents/_genesis/QualityGateAgent/v1/system_prompt.md` — runs `./ai validate` via `shell_exec`.
- `@/Users/yiminguo/shadow-gentcore/harness/core/genesis_verifier.py` implements the same checks in-process (~4.9KB). `verify_genesis_output()` does schema + directory + workflow validation.
- Out-of-process path: CLI-on-PATH assumption, subprocess fork latency (~150ms), larger blast surface for platform-rule false-positives.

**Fix (G5):** register `genesis_verifier.verify_genesis_output()` as a Python tool and call directly. Keep CLI `./ai validate` for users.

---

**G-RST: `reset_points` is dead config.**

- `@/Users/yiminguo/shadow-gentcore/workflows/genesis/genesis_build.yaml:102-105` — `reset_points: [map, architect, build]`.
- `@/Users/yiminguo/shadow-gentcore/harness/core/composition_engine.py` — no reference to `reset_points`. Resume uses `_state_store.load_step()` per step name, not per reset point.

**Fix:** wire reset_points (rollback target when downstream step exhausts retries) OR delete the field with a deprecation note. Recommend wire — it's a useful abstraction for multi-layer pipelines.

---

**G-TDI: ToolDiscovery re-scans every run.**

- `@/Users/yiminguo/shadow-gentcore/agents/_genesis/ToolDiscoveryAgent/v1/system_prompt.md` directs `search_files` across `agent-tools/packs/`.
- ~42 packs × ~5 tools/pack = ~200 manifest reads every genesis run. Packs have no `provides:` tags, so the LLM guesses at capability fit from descriptions.

**Fix (B3):** tag packs with `provides: [capability, …]`, `requires_env: […]`, `typical_use_case: […]`. ToolDiscovery reads a cached index built from these tags.

### Factory vs Genesis Builder overlap (B8)

Both write agent manifests + prompts to a directory tree. They diverge in upstream inputs:

|  | `_genesis/AgentBuilderAgent` | `_factory/AgentFactoryAgent` |
|--|------------------------------|------------------------------|
| Upstream | Architect design | LearnAgent scan + ContextAgent standards |
| Entry workflow | `genesis_build.yaml` | `learn_and_create.yaml` |
| Output | same domain tree | same domain tree |
| Retry loop | QualityGate → Builder | Validate → AgentFactory |

**Recommended role split (B8):**
- **Builder** (`_genesis/`) = first-time genesis from an Architect design; full domain bootstrap.
- **Factory** (`_factory/`) = on-demand synthesis of **a single agent** from a `CapabilityRecipe` for T4-tier gaps (no pack, no recipe, no stage covers it). Takes `{goal, tools, context}`; writes one `AgentName/v1/*` directory.

`workflows/factory/learn_and_create.yaml` and `workflows/genesis/genesis_build.yaml` can converge (**B6**) into one pipeline with branches for existing vs new domain.

---

## 3. 1B — Harness & output consistency deep dive

### 🔴 H3 (critical) — Permission engine dead wiring

`@/Users/yiminguo/shadow-gentcore/harness/core/rule_engine.py:1-16` advertises a 6-layer model:

```
1. Platform rules (config/rules.yaml → platform:) — NON-NEGOTIABLE
2. Category defaults (config/rules.yaml → category_overrides:)
3. Domain rules (domain.yaml → workspace_policy)
4. Agent rules (agent_manifest.yaml → permissions, constraints)
5. Workflow rules (workflow step → permissions_override)
6. Runtime rules (TaskEnvelope → overrides)
```

Actual wiring evidence:

- `@/Users/yiminguo/shadow-gentcore/harness/core/tool_executor.py:27` — `self._rule_context: Any = None  # Set by CompositionEngine per-step`
- `@/Users/yiminguo/shadow-gentcore/harness/core/tool_executor.py:33-35` — `def set_rule_context(self, context: Any) -> None: self._rule_context = context`
- **Grep for `set_rule_context` across `harness/` returns only the definition itself.** No caller.
- `@/Users/yiminguo/shadow-gentcore/harness/core/tool_executor.py:52-54` — `self._rule_engine.check_tool_call(tool_name, arguments, self._rule_context)` passes `None`.
- `@/Users/yiminguo/shadow-gentcore/harness/core/rule_engine.py:104` — `ctx = context or RuleContext()` → empty context when None.
- `@/Users/yiminguo/shadow-gentcore/harness/core/rule_engine.py:286` — `cat_overrides = self._data.get("category_overrides", {}).get(ctx.agent_category, {})` → `{}` when `agent_category` is empty string.

**Implication.** Layers 2–6 are **dead in production**. Only Layer 0 (tool lockdown) + Layer 1 (platform `blocked_commands`, `blocked_content_patterns`, forbidden paths, workspace bounds) + the trusted-paths fast-path actually gate tool calls. Any agent manifest's `permissions: {shell_command: deny}` is **ignored at runtime**.

This is confirmed by:

1. `@/Users/yiminguo/shadow-gentcore/harness/tests/test_rule_engine.py:105-139` — category_override / agent_permission_more_restrictive / most_restrictive_wins tests all pass an explicit `RuleContext(agent_category=…)`. They test the algorithm in isolation, not the production path.
2. The 8 `reasoning`-category agents that declare `shell_command: allow` (`TestRunnerAgent`, `LinterAgent`, `DeployAgent`, `RollbackAgent`, `IntegrationTestAgent`, `PerformanceTestAgent`, `DependencyAnalyzerAgent`, `SecurityScanAgent`) all run shell in practice — because the category layer never fires. Under reasoning's category_override `shell_command: deny`, `_most_restrictive(deny, allow) = deny` would block them.

**Fix (H3).** At the start of each `_execute_step`, populate `ToolExecutor.set_rule_context(RuleContext(agent_category=manifest.category, agent_permissions=manifest.permissions.model_dump(), domain_policy=..., workspace_root=..., trusted_paths=[workspace_root]))`. One call site + a test verifying a `reasoning` agent is blocked from `file_write` when enforcement is on. Ship behind `GENTCORE_ENFORCE_RULES=1` feature flag until domain owners migrate their manifests (some 8 agents will need recategorisation to `fast-codegen` / new `ops` category / etc. — see **S3**).

### 🟠 H2 — Two separate expression evaluators

| Evaluator | File:line | Supports |
|-----------|-----------|----------|
| `CompositionEngine._evaluate_condition` | `@/Users/yiminguo/shadow-gentcore/harness/core/composition_engine.py:553-633` | `true/false`, `status == …`, `has_output`, `score >= N`, `<step>.<field> == <value>` (dotpath), `<step>.<field> is not empty`. **No `and`.** |
| `OutputValidator._eval_check` | `@/Users/yiminguo/shadow-gentcore/harness/core/output_validator.py:244-313` | `true/false`, `<field> op <value>` (6 comparators), dot-notation + `len(…)`, `_exists`, `is not None`. **Supports ` and `.** No `<step>.` prefix. |

Divergent capabilities:

- `validate.validation_passed == false` works in CompositionEngine (gate condition) but fails in OutputValidator.
- `exit_code == 0 and test_count >= 1` works in OutputValidator but fails in CompositionEngine.
- Type coercion subtly different (CompositionEngine string-lowers both sides; OutputValidator tries numeric first).

**Fix (H2).** Single AST-based evaluator in new `harness/core/expr.py` shared by both. Minimal grammar: literals, 6 comparators, `and`/`or`/`not`, `in`, `is (not) None`, dotpath, `len(…)`. Keep the legacy string fast-paths for backward-compatibility.

### 🟠 H1 — `submit_output` final-summary dormant

From earlier exploration: `AnthropicProvider.submit_output` tool-force activates **only when no other tools are declared**. Most agents declare ≥1 tool, so the structured-output enforcement is dormant. LLM is free to return prose that `OutputParser`'s 4 extraction strategies have to recover.

**Fix (H1).** Make `submit_output` a reserved auxiliary tool always present on the final summary call in both `react` and `plan_execute`. It coexists with agent tools, invoked only when the strategy decides "I'm done, here's the structured answer." ~80 LOC in `providers/anthropic.py` + mode adapters + test.

### 🟡 H-PRI — Priority field dead in context assembly

- `@/Users/yiminguo/shadow-gentcore/harness/core/composition_engine.py:190-197` — dependency artifacts injected with `"priority": 8`.
- `@/Users/yiminguo/shadow-gentcore/harness/core/composition_engine.py:424-432` — retry feedback injected with `"priority": 9`.
- `@/Users/yiminguo/shadow-gentcore/harness/core/prompt_assembler.py:137-144` — `_format_context` concatenates in iteration order; **does not sort by `priority`**.

**Impact.** Retry feedback lands below earlier context, potentially truncated when context is large. **Fix:** sort `context_items` by `.get("priority", 0)` descending in `_format_context`. One-liner + test; fold into H2 branch.

### 🟡 H-TDS — ToolDisclosureRouter substring false positives

- `@/Users/yiminguo/shadow-gentcore/harness/core/tool_disclosure.py:143-161` — `detect_and_promote` does `if name in text:` (case-sensitive substring).
- Common tool names (`list`, `read`) auto-promote from any prose mention. Low risk today with the current adapter set; real risk as community MCP names grow.
- Only used by `react` mode (`@/Users/yiminguo/shadow-gentcore/harness/core/modes/react.py`); `plan_execute` does not use L1→L2.

**Fix.** Word-boundary match (`\b<name>\b`) + optional plan_execute integration. Fold into H1.

### 🟡 H5 — Rule engine hot-reload on every call

- `@/Users/yiminguo/shadow-gentcore/harness/core/rule_engine.py:103` — `_hot_reload()` in `check_tool_call` path.
- `@/Users/yiminguo/shadow-gentcore/harness/core/rule_engine.py:353-363` — `_hot_reload` does `stat()` on the rules file each call.
- In a ReAct loop: ~10 tool calls × 8 steps = 80 `stat()` syscalls per agent run.

**Fix.** Debounce to ≥500ms; one time-based guard.

### 🟡 H6 — Unknown tools default to most restrictive

- `@/Users/yiminguo/shadow-gentcore/harness/core/rule_engine.py:325-338` — hardcoded dict of 8 tool names; fallback is `"shell_command"`.
- When **H3** lands, every MCP tool, every AWS CLI tool (`aws_s3`, `cw_query_logs`, …), every domain-declared tool maps to `shell_command`. A `reasoning`-category agent with MCP tools would have all MCP calls denied under category_overrides.

**Fix.** Tie to **B3**: each `ToolPackManifest` declares `action_type: [file_read|file_write|shell_command|network_call|cloud_action|notification|knowledge_retrieval]`. Resolver populates `_tool_to_action` at boot from pack metadata. Clean action taxonomy; no hardcoding.

### 🟢 H7 — Three retry mechanisms to unify

1. `AgentRunner.run_with_reflexion` — intra-agent (same agent re-runs with self-critique).
2. `CompositionEngine._check_gate` with `retry / retry_fresh / rollback` — intra-step.
3. `FeedbackLoop` — cross-step (registered via `register_feedback_loop`).
4. `EvaluatorLoop` — planner/evaluator loop, used by `_orchestrator/`.

Each has its own max-iterations knob; observability scattered across `_execution_log` entries.

**Fix.** `RetryPolicy` protocol in `harness/core/retry_policy.py` defining `{max, cost_budget, should_trigger, on_exhaust}`. Thin adapters on the four existing mechanisms → one surface. Unified log event. Non-breaking.

### 🟢 H-REG — ManifestLoader 3-path fallback

- `@/Users/yiminguo/shadow-gentcore/harness/core/manifest_loader.py` — tries 3 directory patterns per resolve (`_shared/`, `_genesis/`, domain tree).
- `@/Users/yiminguo/shadow-gentcore/harness/core/agent_registry.py` (3KB, partial) exists — flesh out at boot to cache `{agent_id → Path}`. Eliminates directory guessing.

### Harness fix ordering for Phase 2

| Priority | Finding | Fix ID | Blast radius |
|----------|---------|--------|--------------|
| 🔴 P0 | Permission engine dead | H3 | Every tool call |
| 🟠 P1 | Dual condition DSLs | H2 (+ H-PRI folded) | Gates + grading + context ordering |
| 🟠 P1 | `submit_output` dormant | H1 (+ H-TDS folded) | Output consistency |
| 🟡 P2 | Hot-reload every call | H5 | Micro-perf |
| 🟡 P2 | Unknown-tool default | H6 (tied to B3) | MCP unblock |
| 🟡 P2 | Manifest 3-path lookup | H-REG | Boot cleanliness |
| 🟢 P3 | Retry mechanism surface | H7 | Code clarity |

---

## 4. 1C — Stage taxonomy deep dive

### Classification of the 20 `_shared/` stages

Legend: **SWE** = code-specific (tools/prompts bound to code/tests/migrations/packages); **Gen** = generic (adapts via tools+context); **Gen\*** = generic but mis-categorised (category override would misfire once H3 lands).

| # | Agent | Category | Primary mode | Class | Notes |
|---|-------|----------|--------------|-------|-------|
| 1 | `CodeWriterAgent` | fast-codegen | plan_execute | SWE | `forbidden_patterns: ["eval(", "exec(", "__import__"]` Python-leaning |
| 2 | `ReviewerAgent` | reasoning | chain_of_thought | SWE | Reviews code against standards |
| 3 | `TestRunnerAgent` | reasoning | react | SWE + Gen\* | `shell_exec`; reasoning category would block shell under H3 |
| 4 | `LinterAgent` | reasoning | react | SWE + Gen\* | Same category conflict |
| 5 | `SecurityScanAgent` | security-analysis | react | SWE-leaning + Gen\* | Security category blocks `shell_command: allow` under H3 |
| 6 | `IntegrationTestAgent` | reasoning | react | SWE + Gen\* | Category conflict |
| 7 | `PerformanceTestAgent` | reasoning | react | SWE + Gen\* | Category conflict |
| 8 | `MigrationAgent` | fast-codegen | plan_execute | SWE | DB migrations; prompt references alembic/flyway/prisma |
| 9 | `RefactorPlannerAgent` | reasoning | chain_of_thought | SWE | Multi-file refactoring is code-centric |
| 10 | `DependencyAnalyzerAgent` | reasoning | react | SWE + Gen\* | Package managers; category conflict |
| 11 | `DocGeneratorAgent` | fast-codegen | plan_execute | SWE-leaning | API docs / READMEs; generalisable to runbooks |
| 12 | `SpecAnalyzerAgent` | reasoning | chain_of_thought | **Gen** | Analyses requirements; domain-agnostic |
| 13 | `DeployAgent` | reasoning | plan_execute | **Gen\*** | `shell_exec` bound; category blocks under H3 |
| 14 | `RollbackAgent` | reasoning | plan_execute | **Gen\*** | Same |
| 15 | `ComplianceCheckerAgent` | security-analysis | chain_of_thought | **Gen** | Read-only; works for any compliance framework |
| 16 | `NotifierAgent` | cost-optimized | react | **Gen** | Channel-agnostic; no category override defined for `cost-optimized` |
| 17 | `ChangelogAgent` | cost-optimized | react | SWE | Git-history driven |
| 18 | `TicketAgent` | _inferred_ | _inferred_ | **Gen** | Generic based on manifest pattern (assumed) |
| 19 | `ReportAggregatorAgent` | _inferred_ | _inferred_ | **Gen** | Generic based on manifest pattern |
| 20 | `EnvironmentValidatorAgent` | _inferred_ | _inferred_ | **Gen** | Generic based on manifest pattern |

**Count:** ~11 SWE-bound (1,2,3,4,6,7,8,9,10,11,17) + ~9 generic-or-generic-star (5,12,13,14,15,16,18,19,20). That's ~45% genuinely generic today.

### Critical side-effect of H3

When **H3** lands and the permission engine starts enforcing, 8 agents with `category: reasoning` + `shell_command: allow` will be silently blocked at runtime because `reasoning` category override says `shell_command: deny` and `_most_restrictive(deny, allow) = deny`.

**Affected:** `TestRunner`, `Linter`, `Deploy`, `Rollback`, `IntegrationTest`, `PerformanceTest`, `DependencyAnalyzer`, `SecurityScan`.

**Mitigation (S3):** introduce a new `ops` or `execution` category with `shell_command: allow`; re-tag these 8 agents. Bundle S3 with H3 so they ship together.

### Gaps for non-SWE domains

None of the 20 covers:

- **Triage** — classify/prioritise inbound signals (incidents, alerts, tickets, PRs)
- **Investigate** — gather evidence from multiple sources (logs, metrics, traces, docs) and correlate
- **Execute** — execute a procedure/runbook/playbook with confirmation gates
- **Respond** — remediation action based on diagnosed problem
- **Summarize** — distill long evidence into a report
- **Retrieve** — RAG over docs/runbooks/SOPs/knowledge base

These 6 cover incident response, customer-support triage, security-alert triage, data-quality investigation, legal discovery. Swapping tool packs → different domain.

**Fix (B1):** add these 6 to `_shared/` as thin, tool-agnostic, capability-driven stages. Concrete shape in §5.

### Stage tagging (G1/S1)

Today: "stage" is an informal grouping in prose docs only. Agent manifests have no `stage:` field.

**Fix (G1/S1).** Add `Stage` enum to `agent-contracts`:

```python
class Stage(str, Enum):
    ANALYZE = "analyze"        # Spec, Investigate, DependencyAnalyzer, Triage
    GENERATE = "generate"      # CodeWriter, DocGenerator, Migration
    REVIEW = "review"          # Reviewer, Compliance, SecurityScan
    EXECUTE = "execute"        # Deploy, Execute, TestRunner, Linter, IntegrationTest, PerfTest
    RESPOND = "respond"        # Rollback, Respond, Notifier
    SUMMARIZE = "summarize"    # Report, Changelog, Summarize
    RETRIEVE = "retrieve"      # Retrieve, (ToolDiscovery-like)
```

Tag every `_shared/` manifest with `stage:`. Used by the Architect for stage-aware composition (B5) and by the CLI for `./ai run workflow` grouped output (S4).

### Execution-mode coverage

- Today LLM picks (via Architect design) from the 5 declared modes: `react`, `plan_execute`, `chain_of_thought`, `self_ask`, `tree_of_thought`. Self-ask and tree-of-thought have no observed usage.
- Good defaults per stage (S2): writers → `plan_execute`; reviewers/analyzers → `chain_of_thought`; scanners/runners → `react`; retrievers → `react`; triagers → `chain_of_thought`.

### Category coverage gap (S3)

`@/Users/yiminguo/shadow-gentcore/config/categories.yaml:12-45` declares 4 categories: `reasoning`, `fast-codegen`, `security-analysis`, `cost-optimized`. `@/Users/yiminguo/shadow-gentcore/config/rules.yaml:68-85` defines `category_overrides` for 3 (reasoning, fast-codegen, security-analysis — not cost-optimized).

Missing categories:
- `ops` or `execution` — needs `shell_command: allow`, `file_edit: deny`; used by Deploy/Rollback/TestRunner/etc. once H3 lands.
- `compliance` — currently shoe-horned into `security-analysis`; different semantics (permissible network, varied output formats).

**Fix (S3):** add the 2 missing categories + re-tag the 8 affected agents. Ship together with H3.

---

## 5. 1E — Stage + Tools composition readiness

### Thesis (confirmed by team)

Keep stage-based agents. Let **thin, generic stages + rich composable tool packs + domain context** solve any complex domain — including AWS incident response, healthcare ops, fintech reconciliation. Do NOT ship industry-specific agent bundles. The equation is `Agent + Tools + Context → Solution`.

### Readiness scorecard

| Layer | Score | Evidence |
|-------|-------|----------|
| Tool primitives (packs) | **~70%** | `cloud/{aws,aws_advanced,kubectl,terraform}`, `observability/{cloudwatch,datadog,pagerduty}`, `services/{slack,github,jira,confluence}`, `analysis/{snyk,sonarqube,codecov}` already shipped |
| Thin/generic stage agents | **~50%** | ~11 of 20 `_shared/` are code-bound. Missing: **Triage, Investigate, Execute, Respond, Summarize, Retrieve** |
| Composition mechanism | **~40%** | `toolpack://` URI works; Architect invents tool lists per-run instead of composing from a map |
| MCP ecosystem wiring | **~20%** | Adapter + registry exist; only `context7` wired. aws-mcp, k8s-mcp, datadog-mcp, sentry-mcp, opsgenie-mcp absent |
| Runbooks / SOPs as first-class | **~10%** | `context/standards.md` + `context/reference/*.md` exist; no runbook convention, no RAG tool |
| Capability-driven tool resolve | **0%** | No `capability → toolpack` map anywhere |
| **Overall** | **~45%** | Architecture directionally correct — no rewrite needed |

### What's strong already

- Agents are genuinely thin. `@/Users/yiminguo/shadow-gentcore/agents/_shared/CodeWriterAgent/v1/agent_manifest.yaml:6` — "Context-driven — adapts to any language/framework via injected standards."
- `agent-tools` `ToolResolver` handles mixed pack + tool URIs.
- `ToolDisclosureRouter` keeps token cost sane under many declared packs.
- `@/Users/yiminguo/agent-tools/src/agent_tools/adapters/mcp_adapter.py:1-166` — functional MCP adapter (stdio subprocess + JSON-RPC handshake + timeouts; ws:// stubbed).
- `_factory/{LearnAgent, ContextAgent, AgentFactoryAgent, ValidateTestAgent}` + `workflows/factory/learn_and_create.yaml` provide a birthing pipeline for truly novel agents (T4 tier).

### Committed approach — Thin Stages + Rich Tools + Capability Map

| Tier | Content | Philosophy |
|------|---------|-----------|
| **T1 — Core stages** | `_shared/` (today's 20 + 6 new generic: Triage, Investigate, Execute, Respond, Summarize, Retrieve) | One thin agent per stage; behaviour shaped by tools + context |
| **T2 — Industry pack** | `_shared/packs/<industry>/` — **workflow templates + tool-pack bindings + optional prompt overrides**; NOT new agents | A pack is ~100 lines of YAML, not 6 new agents |
| **T3 — Capability map** | `config/capabilities.yaml` — `capability → [toolpack, …]`; `stage_defaults → [capability, …]` | Declarative tool composition instead of prompt-led guessing |
| **T4 — Synthesizer** | `_factory/AgentFactoryAgent` — reserved for truly novel stages no existing core stage + tools can cover | Rare escape hatch, not the default path |
| T5 (optional) — JIT runtime | Behind `--allow-jit-agent` CLI flag | Novel runtime task fallback |

### Capability map shape (B2)

```yaml
# config/capabilities.yaml
capabilities:
  cloud_query:        { packs: [cloud/aws, cloud/aws_advanced, cloud/kubectl] }
  cloud_control:      { packs: [cloud/aws_advanced, cloud/terraform] }
  observability:      { packs: [observability/cloudwatch, observability/datadog] }
  log_analysis:       { packs: [observability/cloudwatch, observability/datadog] }
  alerting:           { packs: [observability/pagerduty] }
  ticketing:          { packs: [services/jira, services/slack] }
  knowledge_retrieval:{ packs: [core/runbook_retrieval, services/confluence] }
  runbook_exec:       { packs: [core/runbook_retrieval, core/shell] }
  code_read:          { packs: [core/filesystem, core/search] }
  code_write:         { packs: [core/filesystem] }

stage_defaults:
  Triage:       [alerting, observability, knowledge_retrieval]
  Investigate:  [cloud_query, observability, log_analysis, knowledge_retrieval]
  Execute:      [cloud_control, runbook_exec, shell_command]
  Respond:      [alerting, ticketing, notification]
  Summarize:    [reporting, ticketing]
  Retrieve:     [knowledge_retrieval]
```

### Tool-pack metadata (B3)

Extend `ToolPackManifest` (additive fields):

```yaml
# agent-tools/src/agent_tools/packs/cloud/aws.yaml
id: "toolpack://cloud/aws"
provides: [cloud_query, cloud_control]           # NEW
action_type: cloud_action                         # NEW — feeds RuleEngine H6
requires_env: [AWS_PROFILE]                      # NEW
typical_use_case: [incident_response, deployment, audit]  # NEW
cost_hint: low                                   # NEW
# ...existing tools: list unchanged...
```

All 42 packs get these fields in one PR (mostly 2-3 lines per pack).

### MCP registry expansion (B6)

Extend `@/Users/yiminguo/shadow-gentcore/config/mcp_servers.yaml` with commented templates so `./ai mcp add aws` is a one-liner:

```yaml
# Uncomment to enable:
# - name: aws
#   command: "npx -y @aws/mcp-server"
#   env: { AWS_PROFILE: "${AWS_PROFILE}" }
#   provides: [cloud_query, cloud_control, observability]
# - name: k8s
#   command: "npx -y @kubernetes/mcp-server"
#   provides: [cloud_query, cloud_control]
# - name: datadog / sentry / opsgenie / grafana …
```

### Runbook convention (B7)

`context/runbooks/*.md` — plain Markdown, optionally frontmatter-tagged:

```markdown
---
id: rds-multi-az-failover
triggers: [rds_instance_unhealthy, rds_failover_required]
estimated_duration: 15m
blast_radius: single_db
approval_required: true
---
# RDS Multi-AZ Failover Runbook
1. Verify ...
2. ...
```

Plus a new `toolpack://core/runbook_retrieval` — RAG over `context/runbooks/*.md`. Execute/Respond stages get this pack by default via the capability map.

### Architect composition (B5)

Prompt rewrite summary:

```
OLD (invents roster):
  "Decide agent boundaries. One agent per distinct responsibility. Minimum 4, maximum 10."

NEW (composes from catalog):
  "Step 1 — Read stage catalog (attached below: 20 existing + 6 new generics with stage: tags).
   Step 2 — For each workflow_process from knowledge_map, pick the minimum-viable stage sequence.
   Step 3 — For each stage, look up required capabilities from stage_defaults (config/capabilities.yaml),
            add domain-specific capabilities from knowledge_map.tools.
   Step 4 — For each capability, resolve toolpack bindings from capabilities map.
   Step 5 — Emit workflow_design with capability_bindings per step; NO new agent manifests unless
            a required stage is missing from the catalog."
```

Output_schema adds per agent_roster entry:
```yaml
decision: { enum: [reuse-core, reuse-with-prompt-override, synthesize-new, ask-human] }
capability_bindings: [{ capability: string, resolved_packs: [string] }]
justification: string
```

Ship behind `--architect-v2` feature flag. Snapshot-test `acme-backend` regeneration for semantic parity before flipping default.

### Audit answers (per §1E committed questions)

| Question | Answer |
|----------|--------|
| Which generic stages? | 6 (Triage, Investigate, Execute, Respond, Summarize, Retrieve) |
| Capability map shape? | Flat `capability → [toolpack]` dict + `stage_defaults → [capability]` dict. Both in one `config/capabilities.yaml`. |
| Tool-pack metadata? | `provides`, `action_type`, `requires_env`, `typical_use_case`, `cost_hint` (all optional, additive) |
| MCP servers to wire? | aws, k8s, datadog, sentry, opsgenie, grafana (commented templates — opt-in) |
| Runbook convention? | Plain Markdown in `context/runbooks/*.md` + frontmatter + `toolpack://core/runbook_retrieval` RAG tool |
| Architect upgrade? | Catalog-driven composition; `capability_bindings` output; `--architect-v2` flag |
| AWS-ops pack minimum? | 1 workflow template + capability bindings + 1 runbook fixture + 1 golden scenario (~300 LOC total) |
| JIT feasibility (T5/B10)? | Defer as experimental; low priority until T1-T4 proven |

---

## 6. AWS-ops pilot walk-through

Concrete end-to-end trace of what happens after B1+B2+B4+B5+B7+B9 land. Zero new agents specific to AWS.

### `domain.yaml` for `acme-aws-ops`

```yaml
name: acme-aws-ops
industry: aws-ops      # NEW (B4)
owner: sre-team
target:
  - path: ../acme-terraform
reference:
  - path: ../golden-runbooks
docs:
  - path: ../confluence-runbook-export
```

### Step-by-step pipeline

**1. `./ai genesis build` runs.** Same entry point as today.

**2. SourceScanner** (unchanged) scans 3 paths. Finds `.tf` files + `runbook_*.md` + Confluence MD/HTML. Produces `scan_result.yaml`.

**3. KnowledgeMapper** (unchanged) classifies:
- Patterns: Terraform + AWS RDS Multi-AZ
- Workflow processes: `incident_triage`, `rds_failover`, `ecs_rollout`, `alert_investigation`
- Tools detected: aws CLI, kubectl, Datadog references in runbooks

**4. ContextEngineer** (unchanged, + B7) writes:
- `context/standards.md` — "Acme uses Terraform + AWS RDS Multi-AZ; incidents triaged within 15min; Slack channel #inc-*; escalation via PagerDuty tier-2."
- `context/architecture.md` — system topology inferred from Terraform.
- `context/runbooks/` — **B7 addition**: runbook files copied with frontmatter normalised; index written.

**5. ToolDiscovery** (enhanced by B3) reads tagged pack index; reports:
- `cloud/aws` (provides: cloud_query, cloud_control)
- `observability/cloudwatch` (provides: observability, log_analysis)
- `observability/pagerduty` (provides: alerting)
- `services/slack` (provides: notification, ticketing)
- `services/jira` (provides: ticketing)
- `core/runbook_retrieval` (provides: knowledge_retrieval, runbook_exec) — B7 addition

**6. AgentArchitect** (B5 v2 prompt) composes:
- Reads `industry: aws-ops` → loads `_shared/packs/aws-ops/workflow_incident_response.yaml` template (B9).
- Reads stage catalog (20 existing + 6 new from B1).
- Reads capability map (B2) → resolves `cloud_query → [cloud/aws]`, `observability → [observability/cloudwatch]`, etc.
- Reads team context from `standards.md` + runbook index.
- Emits roster with `decision: reuse-core` for every stage; `capability_bindings` per step; **zero new agent manifests**.

**7. AgentBuilder** writes:
- `domain.yaml` (team-specific)
- Links to existing `_shared/Triage/InvestigateAgent/ExecuteAgent/SummarizeAgent/v1/*` (no copies needed — manifest `id: _shared/…` resolves via registry)
- `workflows/incident_response.yaml` from the pack template with resolved capability bindings inlined
- `config/capability_bindings.yaml` (domain-specific overrides if any)
- Team runbooks already copied in step 4

**8. QualityGate** validates the generated domain. If B9 includes a golden scenario fixture, it can be replayed in dry-run.

### Generated workflow (the entire AWS-ops deliverable)

```yaml
# agents/workflows/incident_response.yaml (~60 lines — the thin pack in action)
name: incident_response
industry: aws-ops
steps:
  - name: triage
    agent: _shared/TriageAgent/v1
    capabilities: [alerting, observability]
    tools: [observability/pagerduty, observability/cloudwatch]  # resolved via B2
    gate: { condition: "status == success", on_fail: abort }
  - name: investigate
    agent: _shared/InvestigateAgent/v1
    depends_on: [triage]
    capabilities: [cloud_query, observability, log_analysis, knowledge_retrieval]
    tools: [cloud/aws, observability/cloudwatch, core/runbook_retrieval]
    gate: { condition: "has_output", on_fail: retry, max_retries: 2 }
  - name: execute
    agent: _shared/ExecuteAgent/v1
    depends_on: [investigate]
    capabilities: [cloud_control, runbook_exec]
    tools: [cloud/aws_advanced, core/runbook_retrieval]
    gate: { type: approval, message: "Execute remediation action?" }  # human gate
  - name: summarize
    agent: _shared/SummarizeAgent/v1
    depends_on: [execute]
    capabilities: [reporting, ticketing]
    tools: [services/slack, services/jira]
    gate: { condition: "status == success", on_fail: degrade }
budget: { max_tokens: 200000, max_cost_usd: 5.0 }
```

### What changes for a different domain

- **K8s incidents** → swap `cloud/aws` → `cloud/kubectl` in capability map binding; everything else unchanged.
- **Datadog-only shop** → swap `observability/cloudwatch` → `observability/datadog`; stages unchanged.
- **GCP incidents** → add a `cloud/gcp` pack (doesn't exist yet, ~30 lines YAML), tag `provides: [cloud_query, cloud_control]`; capability map auto-routes.
- **Healthcare triage** → swap `cloud/aws` → `services/hl7`, `services/epic`; add `healthcare` industry pack with compliance-focused workflow template. Stages unchanged.

---

## 7. Top findings — ranked

Ranking criterion: `(impact × confidence) / effort`. Severity colours: 🔴 critical, 🟠 high, 🟡 medium, 🟢 low.

| Rank | ID | Severity | Finding | Impact | Confidence | Effort | Fix |
|------|----|----------|---------|--------|-----------|--------|-----|
| 1 | **H3** | 🔴 | `set_rule_context` defined but never called; Layers 2–6 of the permission engine dead | Very high (security + correctness) | High (grep-verified) | S (1 call site + test + flag) | Wire `AgentRunner._execute_step` → `ToolExecutor.set_rule_context(…)` behind `GENTCORE_ENFORCE_RULES=1` |
| 2 | **S3** | 🟠 | Missing `ops` + `compliance` categories; 8 agents mis-categorised (blocked under H3) | High (blocks P0 fix rollout) | High | S | Add 2 categories + re-tag 8 agents. Ship bundled with H3 |
| 3 | **B1** | 🟠 | 6 generic non-SWE stages missing (Triage, Investigate, Execute, Respond, Summarize, Retrieve) | High (unblocks non-SWE domains) | High | M (~400 LOC prompts) | Add to `_shared/` |
| 4 | **B2** | 🟠 | No `capability → toolpack` map | High (deterministic tool binding) | High | S (~200 LOC) | New `config/capabilities.yaml` + loader |
| 5 | **H2** | 🟠 | Dual condition DSLs with divergent grammars | Medium-high (gate correctness) | High | M (AST rewrite + migration) | Single AST-based evaluator in `harness/core/expr.py` |
| 6 | **B5** | 🟠 | Architect invents rosters instead of composing; output_schema drift | High (cost + quality) | High | M (~180 LOC prompt + schema) | Catalog-driven prompt + `capability_bindings` output; `--architect-v2` flag |
| 7 | **G1/S1** | 🟡 | No `stage:` field on agent manifests | Medium (unblocks B5 + S4) | High | S (~40 LOC) | `Stage` enum in `agent-contracts`; tag 20 `_shared/` + 6 new |
| 8 | **B3** | 🟡 | Tool packs lack capability metadata | Medium (Architect guessing) | High | S-M (~180 LOC across 42 packs) | Extend `ToolPackManifest` + tag packs |
| 9 | **H1** | 🟡 | `submit_output` dormant when agent has tools | Medium (output consistency) | Medium | S (~80 LOC) | Always-on final-summary call in react + plan_execute |
| 10 | **B4** | 🟡 | `industry` input orphaned in Architect schema | Medium (unblocks packs) | High | S (~100 LOC) | `industry` field in DomainManifest + `config/industries.yaml` |
| 11 | **G-ARC** | 🟡 | Architect output_schema missing `harness` as required | Medium (Builder gate wiring) | High | XS (1-line schema change) | Fold into B5 |
| 12 | **B9** | 🟡 | End-to-end proof missing for non-SWE | Medium (validates model) | High | M (~300 LOC YAML + fixture) | AWS-ops thin pack + golden scenario |
| 13 | **B7** | 🟡 | Runbooks not first-class | Medium (Execute/Respond need this) | High | M (~150 LOC) | `context/runbooks/` convention + `toolpack://core/runbook_retrieval` |
| 14 | **G5** | 🟡 | QualityGate shells out for validation | Low (hygiene) | High | S (~100 LOC) | Call `genesis_verifier.verify_genesis_output()` in-process |
| 15 | **G6** | 🟡 | Builder targeted retry unenforced | Low-medium (cost) | Medium | S (~80 LOC) | `targeted_files:` allowlist in step config |
| 16 | **G-RST** | 🟡 | `reset_points` is dead config | Low (hygiene) | High | S (~60 LOC wire OR delete) | Wire rollback-to-reset OR remove field |
| 17 | **H-PRI** | 🟡 | `priority` field dead in `_format_context` | Low-medium (context ordering) | High | XS (one-line sort) | Fold into H2 branch |
| 18 | **H6** | 🟡 | Unknown tools default to `shell_command` | Low (depends on H3) | High | S (tied to B3) | Pack metadata feeds action taxonomy |
| 19 | **B6** | 🟡 | MCP registry nearly empty | Low-medium (adoption) | High | XS (~80 LOC yaml templates) | Expand `mcp_servers.yaml` with commented templates |
| 20 | **H5** | 🟢 | Hot-reload on every call | Low (micro-perf) | High | XS (~20 LOC) | Debounce 500ms |
| 21 | **B8** | 🟢 | Factory vs Builder overlap | Low (code clarity) | Medium | S (~80 LOC + docs) | Clean role split: Builder = bootstrap, Factory = synthesise-on-gap |
| 22 | **H-REG** | 🟢 | ManifestLoader 3-path fallback | Low (boot cleanliness) | High | S (~120 LOC) | Flesh out `agent_registry.py` at boot |
| 23 | **H-TDS** | 🟢 | Tool disclosure substring match | Low (future risk) | Medium | XS (regex tweak) | Fold into H1 |
| 24 | **H7** | 🟢 | 3 retry mechanisms with separate knobs | Low (code clarity) | High | M (~200 LOC) | `RetryPolicy` protocol + thin adapters |
| 25 | **G-TDI** | 🟢 | ToolDiscovery re-scans packs every run | Low (cost) | High | S (tied to B3) | Tag packs + cache index |
| 26 | **S4** | 🟢 | Workflow steps not grouped by stage | Low (observability) | High | S (~80 LOC) | Group in generated YAML + CLI output |
| 27 | **G7** | 🟢 | Run record lacks per-step retry/token/cost counts | Medium (observability) | High | S (~60 LOC) | Surface in `execution_log` + CLI printer |
| 28 | **B10** | 🟢 | JIT runtime birthing missing | Low (optional feature) | Low (speculative) | M (~200 LOC) | Behind `--allow-jit-agent` flag — experimental |

---

## 8. Recommended Phase 2 implementation order

Staged rollout as agreed (**full staged 2a → 2e**). Each sub-phase ends with a parity check so existing SWE domains keep regenerating identically.

### Phase 2a — Foundations (zero behaviour change for existing domains)

| Branch | Fix | LOC | Parity |
|--------|-----|-----|--------|
| `fix/G1-stage-enum` | G1/S1 Stage enum + tag all 20 `_shared/` + 6 new | ~90 | Before B1 so tagging is consistent |
| `fix/B1-generic-stages` | B1 — 6 new stage agents | ~400 | None needed; additive |
| `fix/B3-pack-metadata` | B3 — tag 42 packs; extend ToolPackManifest | ~180 | None needed; additive |
| `fix/B6-mcp-templates` | B6 — MCP registry commented templates | ~80 | None needed; additive |
| `fix/B7-runbook-convention` | B7 — runbook conv + RAG toolpack | ~150 | None needed; additive |
| `fix/G-RST-reset-points` | G-RST — wire `reset_points` | ~60 | Existing genesis runs unaffected |
| `fix/G5-qgate-in-process` | G5 — QualityGate uses `genesis_verifier` in-process | ~100 | acme-backend regen identical |

**Exit criterion:** `./ai genesis build` on `acme-backend` produces byte-identical output.

### Phase 2b — Permission engine live (critical + S3 bundled)

| Branch | Fix | LOC | Parity |
|--------|-----|-----|--------|
| `fix/S3-ops-category` | S3 — add `ops` + `compliance` categories; re-tag 8 agents | ~80 | Required before H3 or 8 agents break |
| `fix/H3-enforce-rules` | H3 — wire `set_rule_context` behind `GENTCORE_ENFORCE_RULES=1` | ~80 | Flag-off = no behaviour change; flag-on = new regression suite |
| `fix/H6-action-taxonomy` | H6 — action taxonomy + pack-driven `_tool_to_action` | ~60 | Required for H3 to not misfire on MCP tools |
| `fix/H5-hot-reload-debounce` | H5 — debounce rule reload | ~20 | Micro-perf only |

**Exit criterion:** existing domains run green with flag-off; with flag-on, a new end-to-end test confirms manifests are enforced; 8 re-tagged agents still run their shell tools.

### Phase 2c — Capability resolution (opt-in)

| Branch | Fix | LOC | Parity |
|--------|-----|-----|--------|
| `fix/B4-industry-field` | B4 — `industry` in DomainManifest + `config/industries.yaml` | ~100 | Optional field; no-industry domains unchanged |
| `fix/B2-capability-map` | B2 — `config/capabilities.yaml` + loader | ~200 | No-industry domains skip capability resolution |
| `fix/H2-expr-evaluator` | H2 — unified AST evaluator (+ H-PRI + H-TDS folded) | ~270 | Legacy string fast-paths preserved; snapshot test on gate conditions |
| `fix/H1-submit-output` | H1 — always-on `submit_output` final call | ~80 | Agents still get their normal tools; output consistency improves |
| `fix/G6-targeted-retry` | G6 — Builder `targeted_files:` allowlist | ~80 | Existing behaviour unchanged when allowlist absent |
| `fix/G7-run-record-obs` | G7 — per-step retries/tokens/cost in execution_log | ~60 | Observability only |

### Phase 2d — Architect upgrade + AWS-ops pilot

| Branch | Fix | LOC | Parity |
|--------|-----|-----|--------|
| `fix/B5-architect-v2` | B5 — catalog-driven prompt + `capability_bindings` output; `--architect-v2` flag | ~180 | v1 stays default; `acme-backend` snapshot test for v2 parity |
| `fix/B9-aws-ops-pilot` | B9 — thin AWS-ops pack + golden incident scenario + runbook fixture | ~300 | Proves B1+B2+B4+B5+B7 end-to-end |
| `fix/S4-stage-grouped-printer` | S4 — CLI prints workflow grouped by stage | ~80 | Observability only |

**Exit criterion:** Fresh `industry: aws-ops` genesis produces a working incident-response workflow that dry-runs the golden scenario using **only B1 stages + existing tool packs (capability-resolved via B2) + team runbook (B7)** — zero AWS-specific agents.

### Phase 2e — Hygiene + optional

| Branch | Fix | LOC | Parity |
|--------|-----|-----|--------|
| `fix/B8-factory-builder-split` | B8 — document Builder-vs-Factory roles + refactor | ~80 | Docs + mild refactor |
| `fix/H-REG-agent-registry` | H-REG — flesh out agent_registry.py at boot | ~120 | Boot-time only |
| `fix/H7-retry-policy` | H7 — `RetryPolicy` protocol + adapters | ~200 | Non-breaking; unified logging |
| `fix/G-TDI-pack-index` | G-TDI — cache pack index for ToolDiscovery | ~60 | Tied to B3 completion |
| `fix/B10-jit-agent` *(optional)* | B10 — JIT runtime birthing behind `--allow-jit-agent` | ~200 | Experimental; easy to defer |

### Recommended branch count: ~22 PRs (~2300 LOC total across Phase 2)

Prioritise in this order — **H3+S3+H6 bundled first** (P0 security/correctness), then B1+B3+B6+B7 foundations, then capability resolution, then architect upgrade and pilot, then hygiene.

---

## 9. Out of scope

- Provider plumbing (Bedrock / OpenAI / Claude Code subscription paths).
- Authoring new tools in existing 42 packs (beyond the B3 metadata tagging) — **Exception:** B7 adds `core/runbook_retrieval`.
- CDK / ECS deployment (`gentcore-template/deploy/cdk/`).
- Data-plane / storage migration (`harness/core/storage.py`, `memory_store.py`).
- New provider support, new LLM features (thinking budgets, prompt caching).
- Additional industry packs beyond `aws-ops` (healthcare / fintech / legal / data-analytics) — the pilot proves the model; further packs are follow-on work.

## 10. Assumptions

- Python 3.11+ dev environment; `pytest` suite currently green at ~862 tests.
- Phase 2 fixes stay backward-compatible with `acme-backend` — re-genesis identical through 2a, opt-in behaviour thereafter.
- 20 `_shared/` agents remain source-of-truth for stage examples.
- Phase 2 may add new tests and new config files; additive changes preferred.
- H3 rollout requires coordinating with domain owners to re-categorise any existing domain-generated agents that hit the 8 mis-categorised stages (migration path documented in the H3 PR).

## 11. Appendix A — Key file:line references

### Harness core
- `@/Users/yiminguo/shadow-gentcore/harness/core/composition_engine.py:67-173` — `CompositionEngine.execute` main loop
- `@/Users/yiminguo/shadow-gentcore/harness/core/composition_engine.py:273-526` — `_check_gate` with retry / retry_fresh / rollback / approval strategies
- `@/Users/yiminguo/shadow-gentcore/harness/core/composition_engine.py:553-633` — `_evaluate_condition` DSL
- `@/Users/yiminguo/shadow-gentcore/harness/core/composition_engine.py:635-671` — `_evaluate_route_condition` DSL
- `@/Users/yiminguo/shadow-gentcore/harness/core/composition_engine.py:686-795` — DAG execution + topological sort
- `@/Users/yiminguo/shadow-gentcore/harness/core/output_validator.py:38-95` — `OutputValidator.validate`
- `@/Users/yiminguo/shadow-gentcore/harness/core/output_validator.py:244-313` — `_eval_check` expression parser
- `@/Users/yiminguo/shadow-gentcore/harness/core/rule_engine.py:1-16` — 6-layer model doc
- `@/Users/yiminguo/shadow-gentcore/harness/core/rule_engine.py:93-162` — `check_tool_call` entry
- `@/Users/yiminguo/shadow-gentcore/harness/core/rule_engine.py:276-322` — `_merge_permissions` (the dormant layers)
- `@/Users/yiminguo/shadow-gentcore/harness/core/rule_engine.py:325-338` — hardcoded `_tool_to_action`
- `@/Users/yiminguo/shadow-gentcore/harness/core/tool_executor.py:27-35` — `set_rule_context` definition (uncalled)
- `@/Users/yiminguo/shadow-gentcore/harness/core/tool_disclosure.py:40-185` — L1/L2 router
- `@/Users/yiminguo/shadow-gentcore/harness/core/prompt_assembler.py:137-144` — `_format_context` (priority-blind)

### Genesis agents
- `@/Users/yiminguo/shadow-gentcore/agents/_genesis/AgentArchitectAgent/v1/system_prompt.md:1-139` — Architect prompt
- `@/Users/yiminguo/shadow-gentcore/agents/_genesis/AgentArchitectAgent/v1/agent_manifest.yaml:47-195` — output_schema (drift vs prompt)
- `@/Users/yiminguo/shadow-gentcore/agents/_genesis/AgentBuilderAgent/v1/system_prompt.md:1-145` — Builder prompt (+ "targeted retry" rule line 142)
- `@/Users/yiminguo/shadow-gentcore/workflows/genesis/genesis_build.yaml:1-127` — full pipeline

### Tool packs + config
- `@/Users/yiminguo/agent-tools/src/agent_tools/packs/cloud/aws.yaml:1-30` — AWS pack
- `@/Users/yiminguo/agent-tools/src/agent_tools/packs/observability/cloudwatch.yaml:1-22` — CloudWatch pack
- `@/Users/yiminguo/agent-tools/src/agent_tools/packs/observability/pagerduty.yaml:1-27` — PagerDuty pack
- `@/Users/yiminguo/agent-tools/src/agent_tools/packs/services/slack.yaml:1-22` — Slack pack
- `@/Users/yiminguo/shadow-gentcore/config/rules.yaml:1-86` — platform rules + category overrides
- `@/Users/yiminguo/shadow-gentcore/config/categories.yaml:1-45` — category model defaults
- `@/Users/yiminguo/shadow-gentcore/config/mcp_servers.yaml:1-57` — MCP server registry (mostly empty)

### Stage manifests (classification evidence)
- `@/Users/yiminguo/shadow-gentcore/agents/_shared/CodeWriterAgent/v1/agent_manifest.yaml:1-51`
- `@/Users/yiminguo/shadow-gentcore/agents/_shared/ReviewerAgent/v1/agent_manifest.yaml:1-38`
- `@/Users/yiminguo/shadow-gentcore/agents/_shared/TestRunnerAgent/v1/agent_manifest.yaml:1-46`
- `@/Users/yiminguo/shadow-gentcore/agents/_shared/DeployAgent/v1/agent_manifest.yaml:1-42`
- `@/Users/yiminguo/shadow-gentcore/agents/_shared/SpecAnalyzerAgent/v1/agent_manifest.yaml:1-41`
- `@/Users/yiminguo/shadow-gentcore/agents/_shared/ComplianceCheckerAgent/v1/agent_manifest.yaml:1-38`

---

## 12. Companion plan

Full plan with sub-phase deliverables, risks, mitigations, parity checks, delivery discipline:
`.windsurf/plans/agent-framework-audit-fee0b6.md`
