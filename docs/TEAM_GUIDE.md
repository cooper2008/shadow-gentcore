# Team Guide: How to Generate Domain Agents from Your Codebase

## Overview

Your team has an existing codebase. You want AI agents that understand your code patterns, follow your standards, and can generate/test/review code for your domain. This guide shows exactly how.

**Two paths:**

| Path | Time | When to use |
|------|------|-------------|
| **Fast Path** | 10 min | Your SDLC is standard: spec → code → lint → test → review. Write standards, compose shared agents. |
| **Full Path** | 30 min | You need agents tuned to unique patterns. Auto-scan your repo and generate everything. |

---

## Fast Path: Compose Shared Agents (Recommended Start)

No factory needed. Just write your domain knowledge and wire up a workflow.

### Step 1: Create domain directory

```bash
mkdir -p my_domain/context my_domain/workflows
```

### Step 2: Write your standards

Create `my_domain/context/standards.md` with your team's actual coding rules:

```markdown
# Payment Service Code Standards

## Language & Tooling
- Python 3.11+, FastAPI, Pydantic v2
- ruff for linting, mypy --strict for type checking
- pytest + httpx.AsyncClient for tests

## Must-Follow Rules
- All functions must have type annotations and docstrings
- All I/O must be async
- Never expose internal IDs — use UUIDs externally
- All money amounts use Decimal, never float
- PCI compliance: never log card numbers

## Architecture
- Router → Service → Repository pattern
- One module per resource (users/, payments/, refunds/)
- Custom exceptions inherit from AppError base class
```

### Step 3: Write domain.yaml

```yaml
name: payment
owner: payment-team
purpose: "Payment service — FastAPI backend with PCI compliance"
version: "0.1.0"
workspace_policy:
  allowed_paths: [src/, tests/]
  forbidden_paths: [.env, secrets/]
default_tool_packs:
  - "toolpack://core/filesystem"
  - "toolpack://core/shell"
  - "toolpack://core/search"
context_files:
  - "context/standards.md"
```

### Step 4: Compose workflow from shared agents

Create `my_domain/workflows/feature.yaml`:

```yaml
name: feature
domain: payment
steps:
  - name: spec
    agent: _shared/SpecAnalyzerAgent/v1
    gate: {condition: "status == success", on_fail: abort}

  - name: code
    agent: _shared/CodeWriterAgent/v1
    depends_on: [spec]
    gate: {condition: "status == success", on_fail: retry, max_retries: 2}

  - name: lint
    agent: _shared/LinterAgent/v1
    depends_on: [code]
    gate: {condition: "status == success", on_fail: retry, max_retries: 1, fallback_step: code}

  - name: test
    agent: _shared/TestRunnerAgent/v1
    depends_on: [lint]
    gate: {condition: "status == success", on_fail: retry, max_retries: 2, fallback_step: code}

  - name: review
    agent: _shared/ReviewerAgent/v1
    depends_on: [test]
    gate: {condition: "status == success", on_fail: degrade}

feedback_loops:
  - name: test_to_code
    from_step: test
    to_step: code
    condition: "test.all_passed == false"
    max_iterations: 2

budget:
  max_tokens: 200000
  max_cost_usd: 10.0
```

### Step 5: Run

```bash
# Validate structure
./ai validate my_domain

# Dry-run (no API key needed)
./ai run workflow my_domain/workflows/feature.yaml \
  --task '{"feature_description": "Add POST /v1/refunds endpoint"}' \
  --dry-run

# Real run
export ANTHROPIC_API_KEY=sk-...
./ai run workflow my_domain/workflows/feature.yaml \
  --task '{"feature_description": "Add POST /v1/refunds endpoint"}'
```

**How it works:** The shared `CodeWriterAgent` reads your `context/standards.md` at runtime and writes code following YOUR standards. Same agent, your knowledge.

---

## Full Path: Auto-Generate from Existing Repo

Use when you want the system to analyze your codebase and generate agents tuned to your patterns.

### The 5-Phase Knowledge Input Workflow

```
Phase 1: SCAN       → LearnAgent reads your repo
Phase 2: AUGMENT    → You review + add domain expertise
Phase 3: COMPOSE    → Generated agents + workflow ready
Phase 4: VALIDATE   → Dry-run, test, fix
Phase 5: OPERATE    → Run against real tasks, iterate
```

---

### Phase 1: SCAN — Auto-Extract Patterns

```bash
./ai learn /path/to/your-repo --domain-name my_service --output-dir ./domains
```

**What happens internally:**

| Step | Agent | What it does | Output |
|------|-------|-------------|--------|
| 1 | LearnAgent | Reads pyproject.toml, scans src/, reads 5-10 files | `scan_result` (tech stack, patterns, conventions) |
| 2 | ContextAgent | Converts scan to human-readable docs | `standards.md` + `architecture.md` |
| 3 | AgentFactoryAgent | Generates domain directory with agents | Complete domain dir |
| 4 | ValidateTestAgent | Checks all files are valid | Pass/fail + fix suggestions |

**What gets generated:**

```
domains/my_service/
  domain.yaml                    ← domain config (tool packs, workspace policy)
  context/
    standards.md                 ← coding standards extracted from your code
    architecture.md              ← architecture patterns found in your code
  agents/
    AnalyzerAgent/v1/
      agent_manifest.yaml        ← reasoning agent, read-only
      system_prompt.md           ← "analyze requirements for this codebase"
    CodeGenAgent/v1/
      agent_manifest.yaml        ← codegen agent, file_write allowed
      system_prompt.md           ← "write code following these patterns"
      grading_criteria.yaml      ← quality checks
    TestAgent/v1/
      agent_manifest.yaml        ← test runner, shell_exec allowed
      system_prompt.md           ← "run tests and report results"
    ReviewAgent/v1/
      agent_manifest.yaml        ← reviewer, read-only
      system_prompt.md           ← "review against extracted standards"
  workflows/
    feature_workflow.yaml        ← analyze → code → test → review chain
```

---

### Phase 2: AUGMENT — Add Your Domain Expertise

The auto-scan captures code patterns but misses **business knowledge**. Teams add:

#### 2a. Edit `context/standards.md`
Add rules the scanner can't detect:

```markdown
## Payment-Specific Rules (TEAM ADDED)
- All money amounts use `Decimal` type, NEVER `float`
- PCI compliance: never log card numbers, mask to last 4 digits
- Idempotency keys required on all POST endpoints
- All payment state changes must be auditable (event sourcing)
- Stripe webhook handlers must verify signatures
```

#### 2b. Edit `context/architecture.md`
Add architecture decisions:

```markdown
## Payment Architecture (TEAM ADDED)
- Payment state machine: PENDING → AUTHORIZED → CAPTURED → REFUNDED
- All state transitions go through PaymentStateMachine.transition()
- Webhook handlers are idempotent — check event_id before processing
- External API calls (Stripe, PayPal) wrapped in adapters under services/gateways/
```

#### 2c. Customize agent system prompts
Edit `agents/CodeGenAgent/v1/system_prompt.md`:

```markdown
## Payment-Specific Code Rules
When generating payment endpoints:
1. Always add idempotency key parameter
2. Wrap Stripe calls in try/except with StripeError handling
3. Use event sourcing pattern for state changes
4. Include webhook signature verification
```

#### 2d. Add grading criteria
Edit or create `agents/CodeGenAgent/v1/grading_criteria.yaml`:

```yaml
criteria:
  - name: uses_decimal_for_money
    type: automated
    check: "no_float_money == true"
    weight: 0.3

  - name: has_idempotency_key
    type: llm_judge
    prompt: "Does every POST endpoint accept an idempotency key?"
    weight: 0.3

  - name: pci_compliant
    type: llm_judge
    prompt: "Is the code PCI compliant? No card numbers logged, masked output?"
    weight: 0.4

threshold: 0.85
```

---

### Phase 3: COMPOSE — Build Your Workflow

**Option A: Use generated workflow as-is**
The factory generates a standard workflow. If it fits your SDLC, use it directly.

**Option B: Switch to shared agents**
Replace generated agents with `_shared/` agents for simplicity:

```yaml
# Change this:
  - name: code
    agent: my_service/CodeGenAgent/v1

# To this (uses shared agent + your domain context):
  - name: code
    agent: _shared/CodeWriterAgent/v1
```

**Option C: Hybrid — shared agents + custom domain-specific agent**
Keep shared agents for standard tasks, add domain-specific agents for unique needs:

```yaml
steps:
  - name: spec
    agent: _shared/SpecAnalyzerAgent/v1        # shared

  - name: code
    agent: _shared/CodeWriterAgent/v1           # shared

  - name: payment_validation
    agent: my_service/PaymentValidatorAgent/v1  # CUSTOM domain agent

  - name: test
    agent: _shared/TestRunnerAgent/v1           # shared

  - name: review
    agent: _shared/ReviewerAgent/v1             # shared
```

---

### Phase 4: VALIDATE — Test Before Using

```bash
# 1. Check structure
./ai validate domains/my_service

# 2. Dry-run the workflow (no API key needed)
./ai run workflow domains/my_service/workflows/feature_workflow.yaml \
  --task '{"feature_description": "Add refund endpoint"}' \
  --dry-run

# 3. Test a single agent
./ai run agent _shared/CodeWriterAgent/v1 \
  --task "Add POST /v1/refunds" \
  --domain domains/my_service \
  --dry-run

# 4. Real execution (needs API key)
export ANTHROPIC_API_KEY=sk-...
./ai run workflow domains/my_service/workflows/feature_workflow.yaml \
  --task '{"feature_description": "Add POST /v1/refunds endpoint"}'
```

---

### Phase 5: OPERATE — Run + Iterate

#### Register your domain
```bash
# Add to workspace so other teams can see it
./ai domain register domains/my_service --type external
./ai workspace  # verify it appears
```

#### Collect feedback and improve
After running workflows against real tasks:

1. **Check quality scores** — OutputValidator reports scores per grading criterion
2. **Review audit log** — RuleEngine logs every tool call decision
3. **Improve standards** — Add rules for patterns the agents get wrong
4. **Improve prompts** — Add examples of correct output to system prompts
5. **Re-learn** — Run `./ai learn` again to pick up new patterns from recent code

#### Continuous improvement loop
```
Run workflow → Review output quality → Update standards.md → Run again
                                    → Update system_prompt.md → Run again
                                    → Add grading criteria → Run again
```

---

## Team Scenario Walkthroughs

### Team A: Payment Backend (Python/FastAPI)

```bash
# Phase 1: Scan
./ai learn /repos/payment-api --domain-name payment --output-dir ./domains

# Phase 2: Augment (edit these files)
vim domains/payment/context/standards.md      # Add: PCI rules, Decimal for money
vim domains/payment/context/architecture.md   # Add: state machine, event sourcing

# Phase 3: Compose (use generated workflow or switch to shared agents)
cat domains/payment/workflows/feature_workflow.yaml   # review

# Phase 4: Validate
./ai validate domains/payment
./ai run workflow domains/payment/workflows/feature_workflow.yaml \
  --task '{"feature_description": "Add refund endpoint"}' --dry-run

# Phase 5: Operate
export ANTHROPIC_API_KEY=sk-...
./ai run workflow domains/payment/workflows/feature_workflow.yaml \
  --task '{"feature_description": "Add POST /v1/refunds with Stripe integration"}'
```

**Knowledge input at each phase:**
| Phase | Knowledge added | Source |
|-------|----------------|--------|
| Scan | Tech stack, file structure, naming, imports | Auto (from code) |
| Augment | PCI rules, Decimal policy, state machine | Human (team expertise) |
| Compose | Workflow structure, gates, feedback loops | Human (process design) |
| Validate | Bug fixes to generated manifests | Human (review) |
| Operate | Quality feedback, improved prompts | Human + automated (scores) |

### Team B: Frontend Dashboard (React/TypeScript)

```bash
# Phase 1: Scan
./ai learn /repos/dashboard-ui --domain-name dashboard --output-dir ./domains

# Phase 2: Augment
vim domains/dashboard/context/standards.md
# Add: Design system tokens, accessibility rules, React Query patterns
# Add: Component naming (PascalCase), hook patterns (use* prefix)
# Add: No any types, explicit Props interfaces

vim domains/dashboard/context/component-guide.md   # Create new context file
# Add: Component template, data-fetching template, test template

# Update domain.yaml to include new context file:
vim domains/dashboard/domain.yaml
# context_files:
#   - "context/standards.md"
#   - "context/component-guide.md"    ← add this

# Phase 3: Use shared agents (fast path)
cat > domains/dashboard/workflows/component.yaml << 'EOF'
name: component
domain: dashboard
steps:
  - name: code
    agent: _shared/CodeWriterAgent/v1
    gate: {condition: "status == success", on_fail: abort}
  - name: lint
    agent: _shared/LinterAgent/v1
    depends_on: [code]
    gate: {condition: "status == success", on_fail: retry, max_retries: 2, fallback_step: code}
  - name: test
    agent: _shared/TestRunnerAgent/v1
    depends_on: [lint]
    gate: {condition: "status == success", on_fail: retry, max_retries: 2, fallback_step: code}
  - name: review
    agent: _shared/ReviewerAgent/v1
    depends_on: [test]
    gate: {condition: "status == success", on_fail: degrade}
budget:
  max_tokens: 150000
  max_cost_usd: 8.0
EOF

# Phase 4-5: Validate and run
./ai validate domains/dashboard
./ai run workflow domains/dashboard/workflows/component.yaml \
  --task '{"task_description": "Build UserProfile card component with avatar, name, role"}' \
  --dry-run
```

### Team C: Data Pipeline (PySpark/Airflow)

```bash
# Phase 1: Scan
./ai learn /repos/data-pipeline --domain-name data_pipeline --output-dir ./domains

# Phase 2: Augment
vim domains/data_pipeline/context/standards.md
# Add: Schema validation rules (Great Expectations)
# Add: Partitioning strategy (by date)
# Add: SLA requirements (pipeline must complete in < 30min)
# Add: Data quality checks required before write

# Phase 3: Hybrid — shared agents + custom DAGBuilder
mkdir -p domains/data_pipeline/agents/DAGBuilderAgent/v1

# Create custom agent for Airflow DAG generation
cat > domains/data_pipeline/agents/DAGBuilderAgent/v1/agent_manifest.yaml << 'EOF'
id: data_pipeline/DAGBuilderAgent/v1
domain: data_pipeline
category: fast-codegen
version: "1.0.0"
description: "Generates Airflow DAG definitions following team patterns"
system_prompt_ref: system_prompt.md
execution_mode:
  primary: plan_execute
  max_plan_steps: 5
tools:
  - name: file_write
    pack: "toolpack://core/filesystem"
  - name: file_read
    pack: "toolpack://core/filesystem"
  - name: search_code
    pack: "toolpack://core/search"
permissions:
  file_edit: allow
  file_create: allow
  shell_command: deny
input_schema:
  type: object
  required: [pipeline_description]
  properties:
    pipeline_description: {type: string}
output_schema:             # Guarantees structurally identical JSON on every run
  type: object             # (schema injected in prompt + AnthropicProvider forces it)
  required: [dag_file, summary]
  properties:
    dag_file: {type: string}
    summary: {type: string}
EOF

# Write custom system prompt with Airflow expertise
cat > domains/data_pipeline/agents/DAGBuilderAgent/v1/system_prompt.md << 'EOF'
You are DAGBuilderAgent, an Airflow DAG specialist.

## Role
Generate Airflow DAG files following team patterns from context/standards.md.

## Process
1. Read existing DAGs for patterns (search dags/ directory)
2. Plan the DAG structure (tasks, dependencies, schedule)
3. Write the DAG Python file
4. Include data quality checks (Great Expectations)

Apply all standards from context.
EOF

# Phase 4-5: Validate and run
./ai validate domains/data_pipeline
```

---

## Decision Guide: When to Use What

| Situation | Use | Why |
|-----------|-----|-----|
| Standard SDLC (spec→code→test→review) | Fast Path + `_shared/` agents | Fastest setup, shared agents handle everything |
| Unique patterns (state machines, compliance) | Full Path + augment | Factory learns your patterns, you add business rules |
| Highly specialized tasks (DAG building, migration) | Hybrid: shared + custom agents | Shared for common tasks, custom for unique ones |
| Multiple repos, same team | One domain, multiple workflows | Share context/standards across workflows |
| Microservices with same patterns | One domain template, clone per service | Same standards, different workspace_policy paths |

---

## Knowledge Input Summary

```
WHAT GETS INPUT                          HOW IT'S INPUT                WHERE IT LIVES
──────────────────────────────────────────────────────────────────────────────────────
Tech stack (language, framework)          Auto (./ai learn)            context/standards.md
Code patterns (naming, imports)           Auto (./ai learn)            context/standards.md
Architecture (layering, modules)          Auto (./ai learn)            context/architecture.md
Business rules (compliance, domain)       Manual (team edits)          context/standards.md
Grading criteria (quality checks)         Manual (team writes)         agents/*/grading_criteria.yaml
Agent behavior (role, process)            Auto + manual edit           agents/*/system_prompt.md
Workflow structure (step order, gates)    Auto + manual edit           workflows/*.yaml
Tool requirements (what tools to use)     Auto (detected from code)    domain.yaml → tool_packs
Quality feedback (what agents get wrong)  Manual (from running agents) context/standards.md updates
```

---

## Manifest Authoring Tips

### Enforce Structured Output with `output_schema`

Add `output_schema` to any agent manifest that must return JSON. Three layers enforce it automatically:
1. The schema is appended as `## Required Output Format` in the system prompt
2. `AnthropicProvider` forces a schema-compliant tool call (`submit_output`)
3. `OutputParser` extracts and type-coerces the result as post-processing

```yaml
output_schema:
  type: object
  required: [files_created, summary]
  properties:
    files_created: {type: array, items: {type: string}}
    summary: {type: string}
```

Same context → identical field names and types on every run. Values still vary (LLM judgment).

### Reduce Token Usage with `level: L1`

Agents that declare many tools can set `level: L1` on tools the LLM rarely needs up-front. L1 tools appear as a one-line text hint (~10 tokens each) instead of a full JSON schema (~70 tokens). When the LLM mentions the tool name in its reasoning, the full schema is automatically promoted for the next call.

```yaml
tools:
  - name: file_read           # default → L2 (full schema always present)
    desc: "Read file contents"
  - name: gh_create_pr
    desc: "Open a GitHub PR"
    level: L1                 # text hint only; promoted when LLM mentions it
  - name: slack_notify
    desc: "Send Slack message"
    level: L1
```

**Rule of thumb**: L2 for tools the agent uses on almost every run; L1 for tools that are conditional or end-of-workflow only.
