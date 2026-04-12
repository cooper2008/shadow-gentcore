# Tasks

This task list implements the approved design for the multi-domain agent framework. Tasks are scoped to the **framework SDK** (`shadow-gentcore`), **shared contracts** (`agent-contracts`), and **shared tool library** (`agent-tools`). Domain-specific agent building is done by teams in their own `domain-*` repos and is NOT part of this task list.

Tasks are grouped by tier and phase, following the tiered implementation mapping in `design.md` Section 16. Each task is atomic: it names the file(s) to create or modify, the acceptance criteria, and required tests.

Status markers: `[ ]` pending, `[-]` in-progress, `[x]` completed.

---

## Tier 1 — MVP: Working Single-Domain Loop

### Phase 0: Foundation (shadow-gentcore)

- [x] 1. Create top-level directory skeleton: `agents/_orchestrator/`, `agents/_maintenance/`, `workflows/cross_domain/`, `workflows/maintenance/`, `harness/core/`, `harness/providers/`, `harness/tools/`, `harness/authoring/`, `harness/templates/`, `harness/bridges/`, `harness/cli/`, `harness/lints/`, `harness/tests/`, `config/environments/`, `examples/backend/`. Add `.gitkeep` to empty leaf dirs. Test: directory structure lint validates all expected paths.
- [x] 2. Create `AGENTS.md` repository map. Short map listing directory purposes, key entry points, link to `docs/ARCHITECTURE.md`. Keep under 80 lines. Test: lint checks existence and length.
- [x] 3. Create `docs/` knowledge tree: `docs/ARCHITECTURE.md`, `docs/PLANS.md`, `docs/QUALITY_SCORE.md`, `docs/RELIABILITY.md`, `docs/SECURITY.md`, `docs/design-docs/`, `docs/exec-plans/active/`, `docs/exec-plans/completed/`, `docs/references/`, `docs/exec-plans/tech-debt-tracker.md`. Test: doc-tree lint validates all required docs exist.
- [x] 4. Create `config/categories.yaml` with `reasoning`, `fast-codegen`, `security-analysis`, `cost-optimized` categories. MVP all point to Anthropic. Include 4-layer override examples. Test: YAML schema validation; unit test loads and parses.
- [x] 5. Create `config/domains.yaml` with domain discovery configuration. Support path-based and package-based entries. Include `./examples/backend` as default. Test: unit test loads and validates.
- [x] 6. Create `config/environments/local.yaml` and `config/environments/cloud.yaml`. Local: filesystem storage, interactive permissions. Cloud: placeholder secret manager, fail-closed. Test: unit test loads both and validates required keys.
- [x] 7. Create `pyproject.toml` for `shadow-gentcore` package. Dependencies: `agent-contracts`, `anthropic`, `pydantic`, `click`, `pyyaml`. Dev deps: `pytest`, `ruff`, `mypy`. Test: `pip install -e .` succeeds.
- [x] 8. Create `Makefile` with targets: `setup`, `lint`, `test`, `agent-run`, `workflow-run`, `validate`, `certify`. All delegate to `harness/cli/`. Test: `make help` exits 0.
- [x] 9. Create CLI entry point `harness/cli/ai.py` with stub subcommands: `domain`, `pack`, `agent`, `workflow`, `validate`, `certify`, `publish`, `run`. Test: `./ai --help` exits 0 and lists subcommands.
- [x] 10. Create replay test harness: `harness/tests/replay/recorder.py` and `harness/tests/replay/player.py`. Recorder serializes provider calls to JSON fixtures; player replays them. Test: unit test records and replays a mock call.
- [x] 11. Create test fixture directories: `harness/tests/fixtures/provider_recordings/`, `harness/tests/fixtures/manifests/`, `harness/tests/fixtures/workflows/`. Test: fixture dir lint.

### Phase 1: Contracts Package (agent-contracts)

- [x] 12. Create `agent-contracts/pyproject.toml`. Zero framework dependencies. Only `pydantic`. Test: `pip install -e .` succeeds.
- [x] 13. Define `DomainManifest` in `agent_contracts/manifests/domain_manifest.py`. Fields: name, owner, purpose, workspace_policy, ports, autonomy_profile, default_tool_packs, version. Export JSON Schema. Test: validates good manifest, rejects bad.
- [x] 14. Define `AgentManifest` in `agent_contracts/manifests/agent_manifest.py`. Fields: id, domain, pack, version, category, capabilities, input_schema_ref, output_schema_ref, permissions, tool_bindings, execution_mode, grading_criteria_ref, constraints, context. Test: validates manifest, rejects missing category.
- [x] 15. Define `CapabilityPackManifest` in `agent_contracts/manifests/pack_manifest.py`. Fields: name, domain, agents, shared_tools, version. Test: validates pack with agent and tool refs.
- [x] 16. Define `WorkflowDefinition` in `agent_contracts/manifests/workflow_def.py`. Fields: name, domain, steps (sequence/DAG), gates, dependencies, reset_points, budget, ports, evaluator_loop, feedback_loops. Test: validates linear and DAG; rejects circular dep.
- [x] 17. Define `ToolManifest` in `agent_contracts/manifests/tool_manifest.py`. Fields: id, adapter_class, auth_mode, timeout, retries, rate_limit, sandbox, credential_source, output_normalization, audit_logging. Test: validates each adapter class.
- [x] 18. Define `ToolPackManifest` in `agent_contracts/manifests/tool_pack_manifest.py`. Fields: id, tools, default_policy. Test: validates tool pack with refs.
- [x] 19. Define `TaskEnvelope` in `agent_contracts/contracts/task_envelope.py`. Fields: task_id, workflow_id, agent_id, input_payload, routing_metadata, budget, timeout, execution_mode_override. Test: create, serialize, deserialize.
- [x] 20. Define `RunRecord` in `agent_contracts/contracts/run_record.py`. Fields: trace_id, task_id, workflow_id, agent_id, version, provider, model, tokens_used, cost, duration, status, failure_reason, tool_usage. Test: create and validate.
- [x] 21. Define `ArtifactRecord` in `agent_contracts/contracts/artifact_record.py`. Fields: artifact_id, type (enum), path, metadata, created_at. Test: create for each type.
- [x] 22. Define `Checkpoint` in `agent_contracts/contracts/checkpoint.py`. Fields: checkpoint_id, workflow_id, step, state_snapshot, artifacts, created_at, resumable. Test: create and serialize.
- [x] 23. Define `FeatureContract` in `agent_contracts/contracts/feature_contract.py`. Fields: contract_id, criteria (PASS/FAIL list), source_agent, target_evaluator. Test: create and grade mock result.
- [x] 24. Define `PortBinding` in `agent_contracts/contracts/port_binding.py`. Fields: provider_domain, provider_port, consumer_domain, consumer_port, schema_ref, version_range. Test: validate compatible and incompatible bindings.
- [x] 25. Define `Category` enum and defaults in `agent_contracts/categories.py`. Enums: reasoning, fast_codegen, security_analysis, cost_optimized. Default model/temp configs. Test: enum values, default configs.
- [x] 26. Define `ExecutionMode` types in `agent_contracts/execution_modes.py`. Enums: react, plan_execute, chain_of_thought, self_ask, tree_of_thought. Config dataclasses for reflection, thinking, budget. Test: mode config create and validate.
- [x] 27. Define `StorageBackend` abstract interface in `agent_contracts/contracts/storage.py`. Methods: save_artifact, load_artifact, save_checkpoint, load_checkpoint, save_run_record, query_run_records, list_artifacts. Test: verify contract with mock.

### Phase 2: Core Harness Engine (shadow-gentcore)

- [x] 28. Implement `PromptAssembler` in `harness/core/prompt_assembler.py`. Combines manifest system_prompt + resolved tools + constraints + permissions + context + task into final LLM messages. Test: assemble from fixture manifest, verify all sections present.
- [x] 29. Implement `ModeDispatcher` in `harness/core/mode_dispatcher.py`. Select ExecutionStrategy based on manifest execution_mode config. Test: dispatch to each strategy type.
- [x] 30. Implement `ToolExecutor` in `harness/core/tool_executor.py`. Parse LLM tool calls, route to adapters, normalize output, report results. Test: execute mock tool call, verify normalized output.
- [x] 31. Implement `ReActStrategy` in `harness/core/modes/react.py`. Think → tool call → observe → repeat. Configurable max_react_steps. Test: replay 3-step cycle.
- [x] 32. Implement `PlanExecuteStrategy` in `harness/core/modes/plan_execute.py`. Phase 1 plan, Phase 2 execute steps. Test: replay plan + 2 steps.
- [x] 33. Implement `ChainOfThoughtStrategy` in `harness/core/modes/chain_of_thought.py`. Single deep reasoning pass, no tool loop. Test: replay single pass.
- [x] 34. Implement `AgentRunner` in `harness/core/agent_runner.py`. Full pipeline: init → prompt assembly → mode dispatch → execution → output validation → artifact collection. Test: replay with fixture manifest.
- [x] 35. Implement `GradingEngine` in `harness/core/grading_engine.py`. Score output against grading_criteria.yaml. Support automated (tool) and llm_judge checks. Weighted scoring. Test: grade with fixture criteria.
- [x] 36. Implement `BudgetTracker` in `harness/core/budget_tracker.py`. Track tokens, cost, wall-clock time. Enforce limits per agent and workflow. Warn at threshold. Test: track usage, trigger limit.
- [x] 37. Implement `ContextEngine` in `harness/core/context_engine.py`. Build context from manifest, task, workspace. Priority ranking, token compaction, reset policy. Test: build context, verify token estimation.
- [x] 38. Implement `Guardrails` in `harness/core/guardrails.py`. Schema validation, command allowlist/blocklist, path bounds, content risk stub. Test: validate good input, reject bad path, reject blocked command.
- [x] 39. Implement `PermissionResolver` in `harness/core/permissions.py`. Resolve permissions from agent, domain, runtime. Local interactive, cloud fail-closed. Audit trail. Test: allow, deny, escalation.
- [x] 40. Implement `MessageBus` in `harness/core/message_bus.py`. Typed inter-agent message passing with PortBinding-compatible payloads. Test: send typed message, verify schema enforcement.
- [x] 41. Implement `HandoffManager` in `harness/core/handoff.py`. Checkpoint at reset points, resume from checkpoint. Test: integration checkpoint and resume.
- [x] 42. Implement `CompositionEngine` (linear MVP) in `harness/core/composition_engine.py`. Execute WorkflowDefinition steps in sequence. Pass artifacts via ports. Enforce gates (retry/abort/escalate/fallback/degrade). Test: integration 3-step linear workflow with gate.
- [x] 43. Define `BaseRuntime` in `harness/core/runtime.py`. Methods: resolve_workspace, resolve_credentials, resolve_permissions, get_output_mode, get_storage_backend. Test: abstract contract.
- [x] 44. Implement `LocalRuntime` in `harness/core/runtime.py`. Interactive permissions, filesystem workspace, env credentials, console output. Test: integration resolve workspace.
- [x] 45. Implement `LocalFilesystemStorage` in `harness/core/storage.py`. Stores under `.harness/runs/<trace_id>/`. Test: integration save/load to temp dir.
- [x] 46. Implement `WorktreeManager` in `harness/core/worktree_manager.py`. Provision isolated worktree per run, cleanup on completion. Test: integration create/write/cleanup.
- [x] 47. Define `BaseProvider` in `harness/providers/base_provider.py`. Methods: chat, stream. Define LLMResponse, LLMChunk dataclasses. Test: interface contract.
- [x] 48. Implement `AnthropicProvider` in `harness/providers/anthropic_provider.py`. Wrap Anthropic SDK with thinking mode, tool use, streaming. Test: replay with recorded fixture.
- [x] 49. Implement `ProviderRouter` (single-provider MVP) in `harness/providers/router.py`. Category → config → provider. MVP always Anthropic. Per-agent override. Test: routes agent to correct config.
- [x] 50. Implement `AgentRegistry` in `harness/core/agent_registry.py`. Discover and load all manifests from configured domain paths. Lookup by domain, pack, agent, tool, workflow. Test: fixture manifests discovery and lookup.
- [x] 51. Implement `ValidationPipeline` in `harness/core/validation_pipeline.py`. Lint, schema check, permission check, structural validation. Test: validate correct manifest, reject invalid.
- [x] 52. Implement `BrowserBridge` stub in `harness/bridges/browser_bridge.py`. Interface for UI inspection. MVP: stub logging. Test: call methods without error.
- [x] 53. Implement `ObservabilityBridge` in `harness/bridges/observability_bridge.py`. Read logs, metrics, traces from local run records. Test: read from fixture dir.
- [x] 54. Implement structured logging in `harness/core/metrics/logger.py`. JSON logging with trace_id, agent_id. Token/cost/duration per run. Test: emit and verify JSON.
- [x] 55. Create architecture lint rules in `harness/lints/rules.py`. Directory structure, manifest presence, doc freshness, dependency direction. Test: detect missing manifest in fixture.

### Phase 3: Example Backend Domain (shadow-gentcore/examples/)

- [x] 56. Create example `backend` domain manifest in `examples/backend/domain.yaml`. Owner, purpose, workspace_policy, ports, autonomy `assisted`. Test: schema validation.
- [x] 57. Create `CodeGenAgent` v1 manifest bundle in `examples/backend/agents/CodeGenAgent/v1/`. agent_manifest.yaml, system_prompt.md, tools.yaml, input/output schemas, constraints, permissions, grading_criteria. Category: fast-codegen. Mode: plan_execute + react. Test: manifest validation + replay.
- [x] 58. Create `ValidateAgent` v1 manifest bundle in `examples/backend/agents/ValidateAgent/v1/`. Category: reasoning. Mode: chain_of_thought + self_ask. Test: replay.
- [x] 59. Create `TestAgent` v1 manifest bundle in `examples/backend/agents/TestAgent/v1/`. Category: reasoning. Mode: react. Tools: test runner CLI ref. Test: replay.
- [x] 60. Create `ReviewAgent` v1 manifest bundle in `examples/backend/agents/ReviewAgent/v1/`. Category: reasoning. Mode: chain_of_thought + self_ask. Test: replay.
- [x] 61. Create `quick_change` workflow in `examples/backend/workflows/quick_change.yaml`. Steps: CodeGen → Validate → Test → Review. Gates with retry/abort. Feedback loop: Test → CodeGen. Test: topology validation; integration E2E replay.
- [x] 62. Create E2E test in `harness/tests/e2e/test_backend_quick_change.py`. Full workflow with replay. Verify agents execute, artifacts produced, run records persisted, gates checked, feedback loop works. Test: passes in CI.
- [x] 63. Verify `make workflow-run WORKFLOW=examples/backend/quick_change` works with replay mode. Test: Makefile target succeeds.

---

## Tier 2 — Platform: Evaluator + Multi-Provider + Authoring Kit

### Phase 4: Evaluator Loop + Quality Loops

- [x] 64. Create `PlannerAgent` v1 in `agents/_orchestrator/PlannerAgent/v1/`. Produces FeatureContract with PASS/FAIL criteria. Mode: plan_execute. Test: replay produces valid FeatureContract.
- [x] 65. Create `EvaluatorAgent` v1 in `agents/_orchestrator/EvaluatorAgent/v1/`. Grades output against FeatureContract. Mode: chain_of_thought + self_ask. Test: replay grades mock output.
- [x] 66. Implement `EvaluatorLoop` in `harness/core/evaluator_loop.py`. Planner → Generator → Evaluator cycle. Configurable max_rounds, threshold. Feed critique back on failure. Test: replay 2-round loop (fail then pass).
- [x] 67. Implement self-critique / reflexion in `AgentRunner`. After inner loop, run GradingEngine → if below threshold → re-run with critique. Controlled by reflection config. Test: replay 2-round reflexion.
- [x] 68. Implement gate retry with feedback in `CompositionEngine`. On gate fail: retry with gate feedback injected. Support all gate actions (retry, abort, escalate_human, fallback, degrade). Test: integration gate retry then pass.
- [x] 69. Implement cross-stage feedback in `CompositionEngine`. Downstream sends structured feedback to upstream step. Max rounds limit. Test: integration TestAgent → CodeGenAgent feedback loop.
- [x] 70. Implement checkpoint-based context reset in `ContextEngine` (extend). Serialize to Checkpoint at reset points, fresh context from checkpoint only. Test: integration no context bleed.

### Phase 5: Multi-Provider + Authoring Kit

- [x] 71. Implement `OpenAIProvider` in `harness/providers/openai_provider.py`. Wrap OpenAI SDK, tool use, streaming. Test: replay with recorded fixture.
- [x] 72. Implement `BedrockProvider` in `harness/providers/bedrock_provider.py`. Wrap AWS Bedrock SDK. Test: replay with recorded fixture.
- [x] 73. Extend `ProviderRouter` for multi-provider. Category-based routing, fallback chains, capability-based, per-agent override. Test: route different agents to different providers; fallback on failure.
- [x] 74. Implement `Scaffolder` in `harness/authoring/scaffolder.py`. Subcommands: domain init, pack create, agent create, workflow create. Generate from templates. Test: integration scaffold new domain, verify files.
- [x] 75. Create scaffold templates in `harness/templates/`. Domain, pack, agent (manifest bundle), workflow templates. Test: template lint validates manifests.
- [x] 76. Implement `Validator` in `harness/authoring/validator.py`. Validate manifests, schemas, ports, topology, tools, permissions. Test: validate good domain, reject broken port ref.
- [x] 77. Implement `Certifier` in `harness/authoring/certifier.py`. Local dry-run, cloud dry-run (simulated), evaluator threshold, observability compliance. Test: integration certify example backend with replay.
- [x] 78. Implement `CompatibilityRegistry` in `harness/authoring/compatibility.py`. Track schema versions and port compatibility. Test: detect breaking schema change.
- [x] 79. Implement `Publisher` in `harness/authoring/publisher.py`. Publish certified domain to repo-local catalog. Record version, ownership, evidence. Test: integration publish and discover.
- [x] 80. Wire CLI to authoring kit in `harness/cli/ai.py`. Connect domain init, validate, certify, publish. Test: integration `./ai domain init test_domain`.
- [x] 81. Package `shadow-gentcore` as pip-installable. Ensure `agent-contracts` is a dependency. Test: `pip install shadow-gentcore` in clean venv works.

### Phase 6: Shared Tool Library (agent-tools)

- [x] 82. Create `agent-tools/pyproject.toml`. Dependencies: `agent-contracts`. Test: `pip install -e .` succeeds.
- [x] 83. Implement `CLIToolAdapter` in `agent_tools/adapters/cli_adapter.py`. Shell commands with timeout, sandbox, output capture. Normalize to ArtifactRecord. Test: integration `echo hello`.
- [x] 84. Implement `MCPToolAdapter` in `agent_tools/adapters/mcp_adapter.py`. Connect to MCP server, invoke, capture. Test: integration with mock MCP.
- [x] 85. Implement `HTTPAPIToolAdapter` in `agent_tools/adapters/http_api_adapter.py`. HTTP with auth, rate limit, retry, timeout. Test: integration with mock HTTP server.
- [x] 86. Implement `ToolResolver` in `agent_tools/resolver.py`. Resolve `tool://` and `toolpack://` refs. Validate availability, creds, permissions. Test: resolve refs, detect missing cred.
- [x] 87. Create language-specific tool packs: `agent_tools/packs/python_build.yaml` (pytest, mypy, ruff, pip), `agent_tools/packs/java_build.yaml` (maven, gradle, checkstyle), `agent_tools/packs/go_build.yaml` (go test, golangci-lint). Test: pack validation.
- [x] 88. Create shared tool packs: `agent_tools/packs/build_core.yaml`, `agent_tools/packs/github_pr.yaml`, `agent_tools/packs/browser.yaml`, `agent_tools/packs/observability.yaml`. Test: pack validation.

---

## Tier 3 — Scale: DAG Execution + Cloud Runtime

### Phase 7: DAG Execution + Cloud Runtime

- [x] 89. Extend `CompositionEngine` for DAG execution. Topological sort, parallel branches, join points. Test: integration diamond DAG.
- [x] 90. Implement `CloudRuntime` in `harness/core/runtime.py`. Non-interactive, fail-closed, secret-manager creds, JSON output, webhooks. Test: integration with mock secret manager.
- [x] 91. Implement workflow-defined reset points in `CompositionEngine`. Checkpoint, compact context, fresh start. Test: integration verify reset.
- [x] 92. Create maintenance workflow definitions in `workflows/maintenance/doc_gardening.yaml`, `workflows/maintenance/quality_scoring.yaml`, `workflows/maintenance/drift_cleanup.yaml`. Test: topology validation.
- [x] 93. Create maintenance agents in `agents/_maintenance/DocGardenerAgent/v1/`, `agents/_maintenance/QualityScoreAgent/v1/`, `agents/_maintenance/DriftCleanupAgent/v1/`. Test: replay for each.
- [x] 94. Implement CI/CD hook interface in `harness/core/ci_hooks.py`. Trigger workflows from CI events. Test: unit simulates CI trigger.
- [x] 95. Implement cross-domain workflow support. Domain discovery from `config/domains.yaml`, cross-domain port resolution. Test: integration two-domain workflow.

---

## Cross-Cutting Tasks

### Documentation

- [x] 96. Write `docs/ARCHITECTURE.md` with full system architecture description including multi-repo model.
- [x] 97. Write authoring guide in `docs/references/authoring-guide.md`. How to scaffold, validate, certify domains.
- [x] 98. Write tool integration guide in `docs/references/tool-integration-guide.md`. How to create adapters and packs.
- [x] 99. Write domain certification guide in `docs/references/certification-guide.md`.
- [x] 100. Write quality loops guide in `docs/references/quality-loops-guide.md`. How to configure grading criteria, gates, feedback, evaluator loop.
- [x] 101. Update `AGENTS.md` after each phase completion.

### Quality and Enforcement

- [x] 102. Create manifest schema JSON exports for all contract types in `agent-contracts`.
- [x] 103. Add doc-freshness lint to CI.
- [x] 104. Add dependency-direction lint to CI.
- [x] 105. Add topology lint for workflow definitions.
- [x] 106. Add schema-naming lint for contracts.
- [x] 107. Create quality scorecard template per domain.
