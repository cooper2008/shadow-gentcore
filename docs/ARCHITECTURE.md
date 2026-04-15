# shadow-gentcore Architecture

## Repository Ecosystem

```
agent-contracts          Pydantic types (AgentManifest, TaskEnvelope, RunRecord)
       |
shadow-gentcore          Core SDK: engines, CLI, 37 built-in agents, 9 workflows
       |
agent-tools              42 tool packs + CLI/MCP/HTTP adapters
       |
gentcore-template        Starter scaffold (clone -> edit 1 line -> run genesis)
       |
domain repos             acme-backend, shop-platform, your-domain, ...
```

---

## The Complete Flow

```
                            DOMAIN TEAM
                               |
                    1. Clone gentcore-template
                    2. Edit domain.yaml (name, owner, purpose)
                    3. Add source code to src/
                               |
                    4. bash scripts/bootstrap.sh
                               |
                               v
         +-------------------------------------------+
         |          GENESIS PIPELINE (7 steps)        |
         |                                            |
         |  scan ──> map ──> discover_tools           |
         |                   engineer_context         |
         |                        |                   |
         |                   architect                |
         |                        |                   |
         |                     build  <── validate    |
         |                   (feedback loop x3)       |
         +-------------------------------------------+
                               |
                    GENERATED OUTPUT:
                    context/standards.md
                    context/architecture.md
                    agents/{Name}/v1/
                      agent_manifest.yaml
                      system_prompt.md
                      grading_criteria.yaml
                    workflows/feature_delivery.yaml
                               |
                    5. git commit & push
                               |
                    6. Use agents:
                       - Local CLI: ai run agent X --task "..."
                       - HTTP API:  curl POST /run/agent
                       - GitHub Actions: agent-task.yml
                       - Docker/ECS: deploy/
```

---

## Genesis Pipeline Detail

Genesis is a 7-step DAG that auto-generates domain agents from your source code.

```
Step 1: SCAN (SourceScannerAgent)
  Input:  your src/, tests/, docs/, pyproject.toml
  Output: tech_stack, dependencies, patterns, conventions
  Mode:   react (reads files iteratively)
  Gate:   retry x2

Step 2: MAP (KnowledgeMapperAgent)
  Input:  scan results
  Output: knowledge_map (what standards, patterns, tools detected)
  Mode:   chain_of_thought (single pass reasoning)
  Gate:   retry x1

Step 3a: DISCOVER TOOLS (ToolDiscoveryAgent) ─┐
  Input:  knowledge_map                        ├─ PARALLEL
  Output: tool_packs, mcp_servers              │
  Gate:   degrade (continue even if fails)     │
                                               │
Step 3b: ENGINEER CONTEXT (ContextEngineerAgent)┘
  Input:  knowledge_map + scan results
  Output: standards.md, architecture.md, glossary.md
  Mode:   plan_execute
  Gate:   retry x2

Step 4: ARCHITECT (AgentArchitectAgent)
  Input:  knowledge_map + tools + context
  Output: agent_roster, workflow_design, tool_assignments
  Mode:   plan_execute
  Gate:   retry x1

Step 5: BUILD (AgentBuilderAgent)
  Input:  architect design + context + tools
  Output: all domain files written to disk
  Mode:   plan_execute (up to 20 react steps)
  Gate:   retry x2
  Feedback: validate -> build (up to 3 iterations)

Step 6: VALIDATE (QualityGateAgent)
  Input:  built domain
  Output: validation_passed, issues, targeted_feedback
  Mode:   react (up to 25 steps)
  Gate:   retry x2, fallback to build
  Feedback: validate -> build (if failed)
            validate -> context (if gaps)

Budget: 500K tokens, $25 max, 1 hour timeout
```

---

## Agent Architecture

### Agent = YAML + Prompt (no code)

Each agent is a directory with 3 files:

```
agents/{AgentName}/v1/
  agent_manifest.yaml       <- config: tools, permissions, mode, schema
  system_prompt.md          <- instructions: what the agent does, how
  grading_criteria.yaml     <- quality: pass/fail thresholds
```

### 37 Built-in Agents

| Layer | Agents | Purpose |
|-------|--------|---------|
| **Genesis (L0)** | 8 agents | Build domain agents from source code |
| **Shared (L1)** | 20 agents | Reusable stage agents for domain workflows |
| **Factory** | 4 agents | Domain scaffolding utilities |
| **Orchestrator** | 2 agents | Cross-domain coordination |
| **Maintenance** | 3 agents | Drift cleanup, doc gardening, quality scoring |

### The 20 Shared Stage Agents

These are small, focused agents that domain workflows compose together:

```
ANALYSIS STAGE:
  SpecAnalyzerAgent        <- Analyzes task requirements
  DependencyAnalyzerAgent  <- Checks dependency impact

PLANNING STAGE:
  RefactorPlannerAgent     <- Plans implementation approach

IMPLEMENTATION STAGE:
  CodeWriterAgent          <- Writes production code
  MigrationAgent           <- Database/schema migrations

QUALITY STAGE (parallel):
  TestRunnerAgent          <- Runs unit/integration tests
  LinterAgent              <- Code style + static analysis
  SecurityScanAgent        <- Vulnerability detection
  ComplianceCheckerAgent   <- Policy compliance
  IntegrationTestAgent     <- End-to-end tests
  PerformanceTestAgent     <- Benchmarks + load tests

REVIEW STAGE:
  ReviewerAgent            <- Code review + suggestions

DEPLOY STAGE:
  DeployAgent              <- Deployment execution
  RollbackAgent            <- Deployment rollback
  EnvironmentValidatorAgent <- Post-deploy verification

DOCUMENTATION STAGE:
  DocGeneratorAgent        <- API docs, README updates
  ChangelogAgent           <- Changelog entries

OPERATIONS STAGE:
  NotifierAgent            <- Slack/email notifications
  TicketAgent              <- Jira/Linear ticket management
  ReportAggregatorAgent    <- Metrics aggregation
```

### Why Small Stage Agents?

Each stage agent has:
- **Small context** (~500 lines standards + task input)
- **Focused output** (just code, or just test results, or just review)
- **Typed contract** (input_schema + output_schema in manifest)
- **Independent grading** (own grading_criteria.yaml)

This design means:
1. Each LLM call is focused on ONE thing (less hallucination)
2. Output is validated per-stage (catch errors early)
3. Agents can run in parallel (test + lint + security = concurrent)
4. Failed stages retry independently (don't re-run everything)
5. Confidence propagates: pipeline_confidence = min(stage_confidences)

---

## Workflow Composition

Domains compose shared agents into DAG workflows:

```yaml
# workflows/feature_delivery.yaml
name: feature_delivery
steps:
  # Stage 1: Analysis
  - name: analyze
    agent: _shared/SpecAnalyzerAgent/v1
    gate: {condition: "status == success", on_fail: retry}

  # Stage 2: Plan
  - name: plan
    agent: _shared/RefactorPlannerAgent/v1
    depends_on: [analyze]
    gate: {condition: "status == success", on_fail: retry}

  # Stage 3: Implement
  - name: code
    agent: _shared/CodeWriterAgent/v1
    depends_on: [plan]
    gate: {condition: "status == success", on_fail: retry, max_retries: 2}

  # Stage 4: Quality (PARALLEL)
  - name: test
    agent: _shared/TestRunnerAgent/v1
    depends_on: [code]
    gate: {condition: "status == success", on_fail: retry}

  - name: lint
    agent: _shared/LinterAgent/v1
    depends_on: [code]
    gate: {condition: "status == success", on_fail: degrade}

  - name: security
    agent: _shared/SecurityScanAgent/v1
    depends_on: [code]
    gate: {condition: "status == success", on_fail: degrade}

  # Stage 5: Review
  - name: review
    agent: _shared/ReviewerAgent/v1
    depends_on: [test, lint, security]
    gate:
      type: approval  # Human approval gate
      message: "Review code changes before merge"

  # Stage 6: Ship
  - name: changelog
    agent: _shared/ChangelogAgent/v1
    depends_on: [review]

feedback_loops:
  - name: review_to_code
    from_step: review
    to_step: code
    condition: "review.approved == false"
    max_iterations: 2

budget:
  max_tokens: 200000
  max_cost_usd: 10.0
```

### Gate Strategies

| Strategy | Behavior |
|----------|----------|
| `retry` | Re-run with feedback from failure |
| `retry_fresh` | Re-run with clean context |
| `rollback` | Re-run from a prior step |
| `degrade` | Continue despite failure |
| `abort` | Stop the workflow |
| `escalate_human` | Pause for human decision |
| `approval` | Pause for human approval (via REST API) |

### Gate Types

| Type | Behavior |
|------|----------|
| `standard` | Evaluate condition, apply on_fail strategy |
| `router` | Dynamic routing: `"output contains code" -> code_review` |
| `approval` | Pause DAG, persist state, expose `/approve` endpoint |

---

## 3-Layer Knowledge Model

```
LAYER 1: ALWAYS INJECTED (every agent, every call)
  context/standards.md      <- coding conventions, patterns, tools
  context/architecture.md   <- system design, data flows
  Limit: ~500 lines to control token usage
  Injected by: PromptAssembler

LAYER 2: ON-DEMAND REFERENCE (agent reads when needed)
  context/reference/*.md    <- API specs, deployment guides, etc.
  Accessed via: file_read tool call
  No limit on size

LAYER 3: TOOLS (~10 generic + domain tool packs)
  file_read, file_write, shell_exec, search_code, list_dir, ...
  Tool packs add domain-specific tools (pytest, ruff, alembic, etc.)
  Resolved via: toolpack:// URIs
```

---

## 6-Layer Permission Engine

Every tool call passes through 6 layers. Most restrictive wins.

```
Layer 1: PLATFORM      config/rules.yaml (platform:)
         NON-NEGOTIABLE: blocks rm -rf, eval(), secrets access
         Cannot be overridden by any lower layer

Layer 2: CATEGORY      config/categories.yaml
         reasoning, fast-codegen, security-analysis, cost-optimized
         Sets model, temperature, thinking budget

Layer 3: DOMAIN        domain.yaml (workspace_policy:)
         file_edit, shell_command, external_api, require_tests

Layer 4: AGENT         agent_manifest.yaml (permissions:)
         Per-agent allow/deny for each tool category

Layer 5: WORKFLOW      workflow step (permissions_override:)
         Step-specific overrides (e.g. deploy step allows shell)

Layer 6: RUNTIME       TaskEnvelope (overrides:)
         Task-time budget, execution_mode overrides

Decision: ANY layer says deny -> DENY (most restrictive wins)
```

---

## Execution Pipeline

```
TaskEnvelope
     |
     v
ManifestLoader
  - Load domain.yaml (workspace_policy)
  - Load agent_manifest.yaml (tools, permissions, mode)
  - Load system_prompt.md
  - Load context/standards.md (Layer 1)
  - Register tool packs
     |
     v
PromptAssembler
  - System prompt + context injection
  - Output schema instruction
  - Tool descriptions
     |
     v
ModeDispatcher
  - Select strategy: react / plan_execute / chain_of_thought
     |
     v
ExecutionStrategy.execute()
  |
  |  LLM Call (provider.chat)
  |     |
  |     v
  |  Tool calls? ──yes──> RuleEngine.check() ──> ToolExecutor.execute()
  |     |                                              |
  |     no                                        result injected
  |     |                                         back into messages
  |     v                                              |
  |  BudgetTracker.consume()                    loop until end_turn
  |     |
  |     v
  |  OutputParser (extract JSON from content)
  |     |
  |     v
  |  OutputValidator (schema + grading)
  |     |
  |     v
  |  Hooks: post_execute (optional)
  |     |
  |     v
  |  MemoryStore.store (optional)
     |
     v
RunRecord {status, output, confidence, tokens_used, duration}
```

---

## Provider Support

| Provider | Auth | Model Default | Use Case |
|----------|------|---------------|----------|
| Anthropic | `ANTHROPIC_API_KEY` | claude-sonnet-4-6 | Default for all |
| AWS Bedrock | `AWS_BEARER_TOKEN_BEDROCK` or IAM | claude-sonnet-4-6 on Bedrock | Higher environments |
| OpenAI | `OPENAI_API_KEY` | gpt-5.4 | Alternative |
| Claude Code | Subscription (auto-detected) | claude-code-subscription | Local dev |
| DryRun | None | dry-run | Testing (free) |

Override: `GENTCORE_PROVIDER=dry-run` or `--dry-run` flag

---

## Testing

| Layer | Tests | What |
|-------|-------|------|
| Unit | 1000+ | Core engines, providers, parsers |
| Security | 32 | Auth, path traversal, env var allowlist |
| Smoke | 40 | Full journey: scaffold -> genesis -> validate -> run |
| E2E | 10+ | Genesis pipeline, cross-domain workflows |
| Total | **1162 tests**, 0 regressions |

Commands:
```bash
make smoke                    # full smoke suite
make smoke-preflight          # pre-flight checks
./ai test smoke --verbose     # CLI with detail
./ai test smoke --domain .    # health check any domain
pytest harness/tests/ -q      # full suite
```

---

## Quick Start (Domain Team)

```bash
# 1. Clone template
git clone https://github.com/cooper2008/gentcore-template my-domain
cd my-domain

# 2. Configure (1 line)
# Edit domain.yaml: set name: my-domain

# 3. Add your code
cp -r ../my-app/src src/
cp -r ../my-app/tests tests/

# 4. Generate agents
export ANTHROPIC_API_KEY=sk-ant-...
bash scripts/bootstrap.sh

# 5. Use agents
ai run agent CodeWriterAgent/v1 --task "Add reviews endpoint" --domain .

# 6. Commit generated agents
git add context/ agents/ workflows/
git commit -m "genesis: initial domain agents"
```
