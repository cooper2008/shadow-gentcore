# Requirements

This spec defines the requirements for a multi-domain agent framework that lets internal teams author, validate, certify, publish, compose, and operate interoperable agents and workflows with minimal human interaction.

## Problem Statement

The organization needs a reusable agent platform rather than a one-off set of built-in agents. Different teams should be able to build their own domain agents by following a shared standard, while the framework guarantees interoperability, isolation, observability, safety, and low-touch execution in both local and cloud environments.

Without a common framework contract:
- teams will create incompatible agent structures and prompt conventions
- cross-team agent collaboration will be brittle and undocumented
- autonomy will be unsafe because permissions, budgets, and quality gates will differ by team
- human operators will remain the integration layer between domains instead of workflows doing that work automatically

## Goals

- Provide a standard way for teams to define domains, capability packs, atomic agents, and workflows.
- Make agent-to-agent communication typed, versioned, and reusable across domains.
- Support both local development and headless cloud execution with parity in behavior.
- Reduce human interaction through guarded autonomy, workflow composition, evaluator loops, and policy-based escalation.
- Give teams a golden path to scaffold, validate, certify, and publish their own domain contributions.
- Preserve strict isolation so independently developed domain agents do not interfere with each other.
- Make tool integration pluggable so teams can attach required CLI tools, MCP tools, and API integrations without modifying the harness core.
- Make repository-local knowledge, mechanical enforcement, and agent legibility first-class so the repo itself becomes the operating environment for autonomous agents.

## Non-Goals

- Building every possible domain agent inside the platform team.
- Supporting arbitrary undocumented prompt conventions between custom agents.
- Allowing unrestricted autonomous execution without permissions, budgets, observability, and auditability.
- Requiring every domain to be fully autonomous from the start.

## Personas

### Platform Maintainer
Owns the harness core, platform rules, templates, certification gates, compatibility rules, and cross-domain composition model.

### Domain Team Author
Builds a new domain or extends an existing one by creating manifests, packs, agents, workflows, schemas, and grading criteria.

### Workflow Operator
Runs local or cloud workflows to build, verify, secure, document, release, or analyze work products.

### Reviewer / Risk Owner
Defines approval policy, autonomy level, permission bounds, and escalation rules for risky actions.

## Functional Requirements

### R1. Standardized Platform Structure

**User Story**
As a platform maintainer, I want every team contribution to follow a predictable structure so that the framework can discover, validate, and orchestrate agents consistently.

**Acceptance Criteria**
- WHEN a new project uses the framework, THE SYSTEM SHALL support a multi-repo architecture with separate repositories for shared contracts (`agent-contracts`), framework SDK (`shadow-gentcore`), shared tool library (`agent-tools`), and team-owned domains (`domain-*`).
- THE SYSTEM SHALL distinguish `domain`, `capability pack`, `atomic agent`, `workflow`, and `harness pattern` as separate first-class concepts.
- WHEN a team creates a new domain, THE SYSTEM SHALL require a domain manifest declaring ownership, ports, permissions, workspace rules, and autonomy profile.
- WHEN a team creates a new capability pack, THE SYSTEM SHALL require a pack manifest that groups related atomic agents for reuse.
- WHEN a team creates a new workflow, THE SYSTEM SHALL store it as an explicit versioned artifact rather than an implicit prompt convention.

### R2. Golden-Path Authoring Experience

**User Story**
As a domain team author, I want scaffolders and templates so I can build agents and workflows without reverse-engineering the framework internals.

**Acceptance Criteria**
- WHEN a team initializes a domain, THE SYSTEM SHALL scaffold the standard domain structure, including manifests, templates, and example documentation.
- WHEN a team creates an atomic agent, THE SYSTEM SHALL scaffold the standard manifest bundle: system prompt, tools, input/output schemas, constraints, permissions, config, and grading criteria.
- THE SYSTEM SHALL provide base agent templates and category configuration so that new agents are defined through configuration manifests rather than code subclasses.
- WHEN a team creates a workflow, THE SYSTEM SHALL scaffold a workflow definition using standard contracts, gates, and references.
- THE SYSTEM SHALL provide templates for domains, packs, workflows, docs, and atomic agents.
- IF a team uses the scaffolding commands, THEN the generated artifacts SHALL already conform to the framework naming and structural conventions.

### R3. Typed Communication Between Agents

**User Story**
As a workflow operator, I want agents from different teams to communicate through typed contracts so that independently built agents can interoperate safely.

**Acceptance Criteria**
- THE SYSTEM SHALL define versioned core contracts including `TaskEnvelope`, `ArtifactRecord`, `Checkpoint`, and workflow definitions.
- WHEN one domain publishes outputs, THE SYSTEM SHALL expose them through typed `provides` ports.
- WHEN another domain consumes those outputs, THE SYSTEM SHALL declare typed `consumes` ports.
- IF a workflow references incompatible schema versions, THEN the SYSTEM SHALL fail validation before execution.
- THE SYSTEM SHALL prohibit undocumented prompt-to-prompt coupling as an interoperability mechanism.

### R4. Domain Isolation

**User Story**
As a platform maintainer, I want domain isolation so that independently developed domain agents cannot accidentally interfere with each other.

**Acceptance Criteria**
- THE SYSTEM SHALL treat each domain as a bounded context with its own workspace, permissions, schemas, and workflow catalog.
- WHEN an agent from one domain executes, THE SYSTEM SHALL restrict write access to that domain's allowed workspace paths.
- WHEN cross-domain analysis is needed, THE SYSTEM SHALL allow read-only access according to declared policy.
- IF an agent attempts to exceed its domain boundary, THEN the SYSTEM SHALL block the action and record an auditable event.
- THE SYSTEM SHALL isolate context windows per workflow or domain execution unit to reduce unrelated context leakage.

### R5. Workflow Composition

**User Story**
As a workflow operator, I want reusable workflows that can compose packs and domains so humans do not have to manually coordinate cross-team work.

**Acceptance Criteria**
- THE SYSTEM SHALL support domain workflows and cross-domain workflows as reusable execution definitions.
- WHEN composing domains, THE SYSTEM SHALL prefer workflow-to-workflow or pack-to-pack composition before dropping to raw atomic agents.
- THE SYSTEM SHALL support sequential and DAG-based workflow execution.
- WHEN a workflow defines gates, THE SYSTEM SHALL block downstream stages until gate conditions pass.
- THE SYSTEM SHALL support parallel execution where ports, permissions, and dependencies allow it.

### R6. Local and Cloud Execution Parity

**User Story**
As a workflow operator, I want the same logical behavior locally and in cloud CI so I can develop, test, and run workflows without re-authoring them.

**Acceptance Criteria**
- THE SYSTEM SHALL support local interactive execution and cloud non-interactive execution using the same framework contracts.
- WHEN a workflow runs locally, THE SYSTEM SHALL allow console output, local inspection, and optional human checkpoints.
- WHEN a workflow runs in cloud mode, THE SYSTEM SHALL emit structured JSON, artifacts, and webhook-friendly outputs for downstream automation.
- THE SYSTEM SHALL resolve credentials differently by runtime environment while preserving workflow behavior.
- THE SYSTEM SHALL support workflow-defined context reset points in both local and cloud modes.

### R7. Progressive Autonomy

**User Story**
As a reviewer or risk owner, I want domains to adopt autonomy gradually so we can reduce human interaction without compromising safety.

**Acceptance Criteria**
- THE SYSTEM SHALL support at least three autonomy profiles: assisted, guarded autonomous, and workflow autonomous.
- WHEN a domain is marked assisted, THE SYSTEM SHALL require human approval for risky actions.
- WHEN a domain is marked guarded autonomous, THE SYSTEM SHALL allow non-interactive execution only within bounded permissions, budgets, and policy constraints.
- WHEN a domain is marked workflow autonomous, THE SYSTEM SHALL allow chained workflows, PR/ticket/webhook actions, and escalation only on policy violations.
- IF a workflow exceeds its allowed autonomy profile, THEN the SYSTEM SHALL stop or escalate according to policy.

### R8. Permission and Policy Enforcement

**User Story**
As a risk owner, I want enforceable permission controls so autonomous workflows remain bounded and reviewable.

**Acceptance Criteria**
- THE SYSTEM SHALL support explicit permission policies for file edits, shell commands, tools, external systems, and review hooks.
- THE SYSTEM SHALL support `ask`, `allow`, and `deny` style permission resolution or equivalent policy outcomes.
- WHEN running in cloud mode, THE SYSTEM SHALL define fail-closed behavior for actions that cannot be interactively approved.
- IF an action is denied by policy, THEN the SYSTEM SHALL fail safely and record an audit trail.
- THE SYSTEM SHALL support policy controls at the agent, pack, domain, and runtime levels.

### R9. Evaluator-Driven Quality Control

**User Story**
As a platform maintainer, I want evaluator-driven quality gates so low-touch workflows can still produce trustworthy outputs.

**Acceptance Criteria**
- THE SYSTEM SHALL support Planner → Generator → Evaluator execution for tasks that benefit from explicit quality review.
- THE SYSTEM SHALL support feature contracts as testable statements that evaluators can grade.
- WHEN a workflow is configured with an evaluator loop, THE SYSTEM SHALL support iterative rounds of build, evaluate, and remediate.
- THE SYSTEM SHALL allow domain-specific grading criteria for subjective and objective domains.
- IF evaluator thresholds are not met, THEN the SYSTEM SHALL fail certification or workflow completion according to policy.

### R10. Validation, Certification, and Publication

**User Story**
As a domain team author, I want clear validation and certification gates so I know when a domain contribution is safe to share with other teams.

**Acceptance Criteria**
- THE SYSTEM SHALL provide validation for manifests, schemas, permissions, and workflow topology.
- THE SYSTEM SHALL support dry-run validation locally using fixtures or golden inputs.
- THE SYSTEM SHALL support cloud dry-run validation for non-interactive execution.
- THE SYSTEM SHALL support certification gates covering compatibility, permissions, evaluator threshold, observability compliance, and communication compliance.
- WHEN a domain passes certification, THE SYSTEM SHALL allow publication to an internal catalog or registry.

### R11. Discoverability and Reuse

**User Story**
As a domain consumer, I want to find and reuse certified domains and workflows built by other teams so I do not have to rebuild the same capability.

**Acceptance Criteria**
- THE SYSTEM SHALL support a discoverable catalog or registry of published domains, packs, workflows, versions, and ownership metadata.
- WHEN a team publishes a domain, THE SYSTEM SHALL retain examples and contract references needed by downstream consumers.
- THE SYSTEM SHALL support repo-local reuse first and future package/plugin reuse later.
- WHEN a workflow is reused across teams, THE SYSTEM SHALL retain compatibility checks against its declared ports and versioned schemas.

### R12. Observability and Run Artifacts

**User Story**
As a workflow operator, I want full execution visibility so autonomous workflows can be debugged, trusted, and audited.

**Acceptance Criteria**
- THE SYSTEM SHALL capture run metadata including agent, workflow, provider, duration, tokens, cost, and status.
- THE SYSTEM SHALL persist first-class artifacts including structured outputs, checkpoints, screenshots, logs, traces, and validation reports where relevant.
- WHEN a workflow fails, THE SYSTEM SHALL retain enough context and artifacts for replay, diagnosis, and remediation.
- THE SYSTEM SHALL expose logs, metrics, and traces to agents through a legible runtime interface.
- THE SYSTEM SHALL support auditability for permission decisions and policy escalations.

### R13. Cost and Budget Control

**User Story**
As a workflow operator, I want budget controls so long-running autonomous workflows stay economically bounded.

**Acceptance Criteria**
- THE SYSTEM SHALL support budget caps per run, per agent, per workflow, and per domain policy.
- WHEN a workflow approaches or exceeds a configured budget, THE SYSTEM SHALL stop, degrade, or escalate according to policy.
- THE SYSTEM SHALL record provider and token usage in the run record.
- THE SYSTEM SHALL allow cost-aware routing decisions through provider categories and fallback policy.

### R14. Maintenance and Entropy Control

**User Story**
As a platform maintainer, I want recurring maintenance workflows so the framework remains legible and coherent as more teams contribute agents.

**Acceptance Criteria**
- THE SYSTEM SHALL support maintenance workflows for doc gardening, quality scoring, and drift cleanup.
- WHEN stale docs, duplicated helpers, or architectural drift are detected, THE SYSTEM SHALL produce actionable outputs or fix PR-ready artifacts.
- THE SYSTEM SHALL support quality scorecards per domain.
- THE SYSTEM SHALL treat maintenance as a normal platform workflow, not an exceptional manual cleanup activity.

### R15. Adoption at Organization Scale

**User Story**
As an engineering leader, I want the platform to be easy for many teams to adopt so we can scale domain contributions without central bottlenecks.

**Acceptance Criteria**
- THE SYSTEM SHALL minimize the amount of framework-specific knowledge a new team needs before contributing a useful domain.
- THE SYSTEM SHALL provide examples, docs, and generated starting points for common domain patterns.
- THE SYSTEM SHALL allow teams to start with assisted workflows and mature toward higher autonomy over time.
- THE SYSTEM SHALL support multiple domains from different teams coexisting without central rewrites to the harness core.
- THE SYSTEM SHALL make the easiest authoring path also the most standards-compliant path.

### R16. Tool and Integration Extensibility

**User Story**
As a domain team author, I want to attach the tools and integrations my agents need, such as build/test CLI tools, GitHub APIs, MCP tools, ticketing tools, browser automation, and cloud CLIs, without modifying the harness core.

**Acceptance Criteria**
- THE SYSTEM SHALL support standard tool declarations in agent manifests and/or reusable tool manifests.
- THE SYSTEM SHALL support at least three tool adapter classes: CLI tools, MCP tools, and HTTP/API tools.
- WHEN a team defines a reusable set of tools, THE SYSTEM SHALL allow it to be published as a shared tool pack or capability reference that multiple agents can reuse.
- WHEN an agent or workflow declares required tools, THE SYSTEM SHALL validate tool availability, credentials, permissions, and runtime compatibility before execution starts.
- IF a required tool is unavailable, misconfigured, or forbidden by policy, THEN the SYSTEM SHALL fail validation or escalate before execution rather than partially executing and discovering the problem late.
- THE SYSTEM SHALL support per-tool policy for timeout, retries, rate limits, sandboxing, credential source, and audit logging.
- THE SYSTEM SHALL normalize tool outputs into framework-readable artifacts or typed workflow data rather than hidden prompt-only state.
- THE SYSTEM SHALL provide scaffolded examples for common integrations including build/test toolchains, GitHub, browser automation, observability, and ticketing systems.
- THE SYSTEM SHALL allow cloud execution to resolve tool credentials and endpoints from runtime-secret configuration rather than local-only developer settings.
- THE SYSTEM SHALL host shared tool adapters and packs in a dedicated `agent-tools` repository with independent versioning.

### R17. Repository Knowledge, Legibility, and Mechanical Enforcement

**User Story**
As a platform maintainer, I want repository-local knowledge and mechanical enforcement so agents can discover rules, inspect evidence, and stay within architectural boundaries without relying on hidden chat context.

**Acceptance Criteria**
- THE SYSTEM SHALL treat `AGENTS.md` as a short repository map and `docs/` as the versioned system of record for architecture, plans, quality, reliability, security, and references.
- THE SYSTEM SHALL keep active and completed execution plans, decision logs, and tech-debt tracking in-repo so future agents can discover prior work.
- THE SYSTEM SHALL provide legible access to UI state, logs, metrics, and traces through runtime bridges or equivalent inspectable interfaces.
- THE SYSTEM SHALL persist first-class evidence artifacts such as screenshots, UI snapshots, traces, validation outputs, and run records where relevant.
- THE SYSTEM SHALL enforce doc freshness, cross-link integrity, stale reference detection, and ownership checks through automated lints or structural checks.
- THE SYSTEM SHALL enforce dependency direction, parse-at-boundary, schema naming, structured logging, and topology rules through automated lints or tests rather than prose alone.

### R18. Execution Modes and Runtime Overrides

**User Story**
As a workflow operator, I want agent reasoning modes to have sensible stage-based defaults but still be overridable per run so I can balance quality, speed, and cost.

**Acceptance Criteria**
- THE SYSTEM SHALL support at least these execution modes: ReAct, Plan-and-Execute, Chain-of-Thought, Self-Ask, Tree-of-Thought, and Reflexion.
- THE SYSTEM SHALL allow each agent to declare a default execution-mode configuration as part of its manifest or config.
- THE SYSTEM SHALL support stage-based defaults so planning, generation, review, testing, security, and documentation stages can map to different default reasoning patterns.
- THE SYSTEM SHALL support composite modes such as plan-then-react, chain-of-thought plus self-ask, and evaluator-loop/reflexion wrappers.
- THE SYSTEM SHALL allow runtime overrides at the agent run or workflow run level without requiring code changes to the agent itself.
- THE SYSTEM SHALL treat evaluator-loop/reflexion as a harness-level wrapper pattern rather than relying on ad-hoc prompt conventions inside each agent.

### R19. Multi-Repo Architecture

**User Story**
As a platform maintainer, I want the framework split across multiple repositories so that shared types, the SDK, shared tools, and team domains can evolve independently.

**Acceptance Criteria**
- THE SYSTEM SHALL use a dedicated `agent-contracts` package for all shared type definitions (manifests, runtime contracts, enums) with zero framework dependencies.
- THE SYSTEM SHALL use a dedicated `shadow-gentcore` repository for the framework SDK (engine, providers, authoring, CLI).
- THE SYSTEM SHALL use a dedicated `agent-tools` repository for shared tool adapters and language-specific tool packs.
- THE SYSTEM SHALL support team-owned `domain-*` repositories that depend only on `agent-contracts` and optionally on `shadow-gentcore` and `agent-tools`.
- THE SYSTEM SHALL support domain discovery at runtime through configuration (path-based and package-based).
- THE SYSTEM SHALL ensure a single dependency direction: contracts ← SDK, contracts ← tools, contracts + SDK + tools ← domains.

### R20. Configuration-Driven Agent Identity

**User Story**
As a domain team author, I want to define agents purely through configuration manifests so I never need to subclass framework code.

**Acceptance Criteria**
- THE SYSTEM SHALL provide a single `AgentRunner` class that can run any agent based solely on its manifest bundle.
- THE SYSTEM SHALL use a `PromptAssembler` to combine system prompt, resolved tools, constraints, permissions, context, and task input into an assembled prompt — making configuration the mechanism for agent identity.
- THE SYSTEM SHALL NOT require Python subclasses, custom runner code, or framework code changes to create a new agent type (codegen, PM, testing, validation, etc.).
- THE SYSTEM SHALL support a 4-layer configuration override: category defaults → agent manifest → workflow step override → runtime override, with deep-merge semantics.

### R21. Multi-Language Agent Support

**User Story**
As a domain team author, I want to build agents that work with any target programming language (Python, Java, Go, etc.) without changing the framework.

**Acceptance Criteria**
- THE SYSTEM SHALL support language-specific tool packs (e.g., `python_build`, `java_build`, `go_build`) that provide the appropriate build, test, and lint tools for each language.
- THE SYSTEM SHALL allow agents to target any programming language by referencing the appropriate tool pack in their manifest, without requiring framework changes.
- THE SYSTEM SHALL keep the harness SDK in Python while supporting agents that generate, test, and validate code in any language.

## Non-Functional Requirements

### NFR1. Reliability
- WHEN a workflow is retried, THE SYSTEM SHALL preserve durable artifacts and checkpoint references needed for resumption.
- THE SYSTEM SHALL fail safely when required manifests, schemas, policies, or credentials are invalid.

### NFR2. Security
- THE SYSTEM SHALL enforce least-privilege defaults for team-contributed domains.
- THE SYSTEM SHALL avoid implicit trust between independently authored domains.

### NFR3. Extensibility
- THE SYSTEM SHALL allow new domains, providers, packs, and workflows to be added without modifying unrelated domain implementations.
- THE SYSTEM SHALL treat provider integration as a seam rather than hardcoding provider behavior into domain authorship.
- THE SYSTEM SHALL allow new tool adapters, tool packs, and external integrations to be added without requiring changes to unrelated domains or workflow definitions.

### NFR4. Legibility
- THE SYSTEM SHALL keep plans, references, architecture docs, and quality rules in-repo so that future agents can discover them.
- THE SYSTEM SHALL prefer inspectable, versioned artifacts over hidden process and tribal knowledge.
- THE SYSTEM SHALL make core execution state inspectable enough that an agent or operator can determine what happened from repository artifacts, logs, traces, and captured evidence.

### NFR5. SDK Packaging
- THE SYSTEM SHALL be pip-installable as `shadow-gentcore` with `agent-contracts` as a dependency.
- THE SYSTEM SHALL allow domain teams to depend on `agent-contracts` alone for type definitions without pulling in the full SDK.
- THE SYSTEM SHALL allow `agent-tools` to be installed independently for shared tool adapters and packs.

## Success Criteria

The framework is successful when:
- at least one non-platform team can scaffold and certify a new domain in their own repo without custom platform code changes
- independently authored domains can communicate through typed ports and run in a shared cross-domain workflow
- a certified workflow can run headlessly in cloud mode with bounded permissions, observability, evaluator-based quality control, and minimal human intervention
- platform maintainers can validate, audit, and evolve the system without relying on undocumented prompt conventions
- at least one team-contributed domain can add a real external integration such as a build CLI, GitHub API, or MCP tool without changing unrelated harness modules
- agents and operators can reconstruct execution intent, evidence, and boundary decisions from repo-local documentation and runtime artifacts without depending on hidden chat history
- the same AgentRunner class can run codegen, PM, testing, and validation agents through configuration alone
- a domain team can build agents targeting Python, Java, or Go by swapping tool packs without framework changes
