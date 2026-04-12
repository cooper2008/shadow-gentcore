# How the GitHub Repos Work Together

Complete reference for the 4-repo (+ template) architecture.

---

## Repo map

```
YOUR_ORG/
├── agent-contracts          framework (platform team)
├── shadow-gentcore          framework (platform team)
├── agent-tools              framework (platform team)
├── gentcore-template        template  (platform team, domain teams clone this)
│
├── acme-backend             domain    (domain team A)
├── payments-service         domain    (domain team B)
└── data-platform            domain    (domain team C)
```

---

## 1. agent-contracts

**What it is:** Shared Pydantic type definitions — the "contract" between repos.

**What it contains:**
```
src/agent_contracts/
  manifests/
    agent_manifest.py     ← AgentManifest model (parsed from agents/*/v1/agent_manifest.yaml)
    workflow_def.py       ← WorkflowDefinition, StepDefinition, GateDefinition, FeedbackLoop
    domain_manifest.py    ← DomainManifest (parsed from domain.yaml)
  contracts/
    task_envelope.py      ← TaskEnvelope (wraps all task inputs)
    execution_event.py    ← Typed event enum for workflow observability
```

**Used by:** shadow-gentcore (imports types), agent-tools (imports types), domain repos (validated against).

**Install:**
```bash
pip install agent-contracts                        # from PyPI
pip install git+https://github.com/ORG/agent-contracts.git  # from GitHub
pip install -e ../agent-contracts                  # local dev
```

**When to update:** When adding new fields to agent manifests, workflow definitions, or domain configs. Bump version and update shadow-gentcore to match.

---

## 2. shadow-gentcore

**What it is:** The core engine, CLI, and genesis agents.

**What it contains:**
```
harness/
  core/
    agent_runner.py         ← Runs a single agent (prompt → LLM → output)
    composition_engine.py   ← DAG executor (multi-step workflows)
    manifest_loader.py      ← Loads + validates YAML manifests, boots engine
    rule_engine.py          ← 6-layer permission merge
    output_validator.py     ← Validates agent output against schema
    output_parser.py        ← 4-strategy JSON extraction
    tool_disclosure.py      ← L1/L2 progressive tool disclosure
  tools/
    builtin.py              ← 122 built-in tool adapters (file, git, HTTP services)
    mcp_loader.py           ← MCP server bridge
  server/
    app.py                  ← FastAPI HTTP server (POST /run/agent, /run/workflow)
    runner.py               ← Async wrappers used by the server
  cli/
    ai.py                   ← CLI entry point (./ai genesis build, ./ai run, ./ai serve)
  providers/
    anthropic_provider.py   ← Claude API
    openai_provider.py      ← OpenAI API
    dry_run.py              ← Mock provider (no API calls)

agents/
  _genesis/                 ← 8 genesis agents (SourceScanner, KnowledgeMapper, etc.)
  _shared/                  ← 20 reusable stage agents
  _orchestrator/            ← Orchestration agents
  _maintenance/             ← Evolution + maintenance agents
  _factory/                 ← Factory agents

workflows/
  genesis/
    genesis_build.yaml      ← Full 7-step genesis pipeline
    genesis_scan.yaml       ← Scan-only
    genesis_evolve.yaml     ← Post-deployment evolution

config/
  workspace.yaml            ← Repo + team registry
  rules.yaml                ← Platform permission rules
  mcp_servers.yaml          ← MCP server config
```

**Install:**
```bash
pip install shadow-gentcore                          # from PyPI
pip install "shadow-gentcore[server]"                # with FastAPI server
pip install "shadow-gentcore[all-providers]"         # with OpenAI + Bedrock
pip install -e .                                     # local dev
```

**CLI commands:**
```bash
./ai genesis build --team <name>    # run genesis pipeline
./ai genesis scan  --team <name>    # scan only
./ai run agent <id> --task "..."    # run single agent
./ai run workflow <path>            # run workflow
./ai serve --domain . --port 8765   # start HTTP server
./ai workspace                      # show workspace status
./ai validate <path>                # validate manifest
```

**Config files in this repo:**

| File | Purpose | Edit? |
|------|---------|-------|
| `config/workspace.yaml` | Register domain repos + team configs | Yes — add new domain teams here |
| `config/rules.yaml` | Platform permission rules (non-negotiable) | Rarely — platform team only |
| `config/mcp_servers.yaml` | MCP server declarations | Yes — add MCP servers |

**Environment variables:**
```bash
GENTCORE_RULES_PATH        # override default config/rules.yaml path
GENTCORE_MCP_CONFIG_PATH   # override default config/mcp_servers.yaml path
```

---

## 3. agent-tools

**What it is:** Tool pack definitions and adapters for domain agents.

**What it contains:**
```
src/agent_tools/
  packs/              ← YAML tool pack definitions (toolpack:// URIs)
  adapters/
    http_api.py       ← HTTPAPIToolAdapter (replaces curl-based tools)
    mcp_adapter.py    ← MCPToolAdapter
  resolver.py         ← ToolResolver (resolves toolpack:// URIs)
```

**Used by:** shadow-gentcore's `boot_engine()` resolves `toolpack://` URIs via ToolResolver.

**Install:**
```bash
pip install agent-tools
pip install -e ../agent-tools  # local dev
```

---

## 4. gentcore-template

**What it is:** Starter template for domain teams. Clone this, edit 1 line, run genesis.

**Minimum config (after clone):**
1. Edit `domain.yaml` → set `name:` 
2. Set `ANTHROPIC_API_KEY` env var
3. Run `bash scripts/bootstrap.sh`

**Files that need to exist before genesis runs:**
```
domain.yaml                   ← name (required), industry (optional)
config/provider.yaml          ← pre-configured, no edit needed
src/                          ← your source code (or point via domain.yaml)
tests/                        ← your tests
```

**Files genesis generates (do not pre-fill):**
```
context/architecture.md       ← auto from scan
context/standards.md          ← auto from scan + AI best practices
context/glossary.md           ← auto from docs
agents/                       ← domain agents
workflows/                    ← workflow definitions
```

**Deploy files (already in template, configure via GitHub Secrets + Variables):**
```
deploy/Dockerfile             ← container image
deploy/docker-compose.yml     ← local/server deployment
deploy/cdk/             ← AWS ECS Fargate IaC
.github/workflows/
  deploy-agents.yml           ← CI: push to ECR + deploy ECS
  agent-task.yml              ← dev: trigger agent runs (HTTP or local CLI)
```

---

## 5. Domain team repos (e.g. acme-backend)

**What it is:** A domain team's repo, cloned from gentcore-template and customized.

**Structure:**
```
acme-backend/
├── domain.yaml               ← name: acme-backend, industry: ecommerce
├── config/
│   └── provider.yaml         ← model: claude-sonnet-4-6
├── src/acme_api/             ← FastAPI application code
│   ├── main.py
│   ├── models/
│   ├── routers/
│   └── database/
├── tests/                    ← pytest test suite
├── migrations/               ← Alembic migrations
│
├── context/                  ← GENERATED by genesis
│   ├── architecture.md       ← system architecture (auto)
│   ├── standards.md          ← coding standards + best practices (auto)
│   └── reference/            ← detailed reference docs (auto)
│
├── agents/                   ← GENERATED by genesis
│   ├── FastAPICodeGenAgent/v1/
│   │   ├── agent_manifest.yaml
│   │   ├── system_prompt.md
│   │   └── grading_criteria.yaml
│   ├── TestRunnerAgent/v1/
│   └── ...
│
├── workflows/                ← GENERATED by genesis
│   ├── feature_delivery.yaml
│   ├── bug_fix.yaml
│   └── quick_change.yaml
│
├── deploy/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── cdk/            ← AWS ECS deployment
│
└── .github/workflows/
    ├── deploy-agents.yml     ← deploys agent server to ECS
    └── agent-task.yml        ← triggers agent runs
```

---

## Config reference: what goes where

### domain.yaml (domain repo root)
```yaml
name: acme-backend              # REQUIRED — matches team name in workspace.yaml
industry: ecommerce             # optional — used by genesis for context
version: "1.0.0"

# Optional: override auto-discovery
# reference:                    # repos to deep-scan for standards
#   - path: ../golden-repo
# target:                       # repos to structure-scan
#   - path: ./src
# docs:                         # docs to scan
#   - path: ./context
#     type: documents

capabilities: [fastapi, sqlalchemy, postgresql, alembic, pytest, docker]
# ↑ auto-inferred from scan if omitted

workspace_policy:
  isolation: standard
  max_file_size_mb: 10
```

### config/provider.yaml (domain repo)
```yaml
provider: anthropic              # anthropic | openai | bedrock
model: claude-sonnet-4-6         # model ID
max_tokens: 8192
api_key_env: ANTHROPIC_API_KEY   # env var name — never put key here
```

### config/workspace.yaml (shadow-gentcore)
```yaml
# Register domain repos here so ./ai genesis build --team <name> works
repos:
  acme-backend:
    path: ../acme-backend
    role: domain
    description: "Acme Corp FastAPI backend"
    install: pip install -e ../acme-backend

teams:
  acme-backend:
    industry: ecommerce
    trusted: true
    reference:
      - path: ../acme-backend/src
        label: "Acme source"
    target:
      - path: ../acme-backend/src
      - path: ../acme-backend/tests
    docs:
      - path: ../acme-backend/context
        type: documents
    focus: [fastapi, sqlalchemy, postgresql]
    output: ../acme-backend
    provider_config: ../acme-backend/config/provider.yaml
```

### GitHub Secrets and Variables (per domain repo)

| Name | Type | Value | Required |
|------|------|-------|---------|
| `ANTHROPIC_API_KEY` | Secret | `sk-ant-...` | Yes (local CLI mode) |
| `AGENT_API_KEY` | Secret | random string | Yes (HTTP auth) |
| `AWS_DEPLOY_ROLE_ARN` | Secret | IAM role ARN | Yes (AWS deploy) |
| `AWS_REGION` | Variable | `us-east-1` | Yes (AWS deploy) |
| `ECR_REPOSITORY` | Variable | `acme-backend-agents` | Yes (AWS deploy) |
| `ECS_CLUSTER` | Variable | `acme-backend-agents` | Yes (AWS deploy) |
| `ECS_SERVICE` | Variable | `acme-backend-agents` | Yes (AWS deploy) |
| `ECS_TASK_DEFINITION` | Variable | `acme-backend-agents` | Yes (AWS deploy) |
| `AGENT_SERVICE_URL` | Variable | ALB DNS URL | Optional (enables HTTP mode) |

---

## Complete flow: from zero to deployed agents

```
Platform team                        Domain team
──────────────────────────────────────────────────────────────────
1. Publish framework packages to PyPI/GitHub
   agent-contracts, shadow-gentcore, agent-tools

2. Push gentcore-template to GitHub

                                     3. Clone template
                                        git clone gentcore-template acme-backend

                                     4. Edit domain.yaml (1 line: name)

                                     5. Add source code to src/

                                     6. Run genesis locally:
                                        bash scripts/bootstrap.sh
                                        → generates context/, agents/, workflows/

                                     7. Commit generated files

                                     8. Set up AWS (one-time):
                                        cd deploy/cdk
                                        npm install
                                        npx cdk deploy --context domainName=acme-backend \
                                          --context vpcId=vpc-xxx ...

                                     9. Set GitHub Secrets + Variables

                                     10. Push to main
                                         → deploy-agents.yml triggers
                                         → Docker image built + pushed to ECR
                                         → ECS service updated
                                         → Agent API Server live at ALB URL

                                     11. Use agents:
                                         gh workflow run agent-task.yml \
                                           -f agent=FastAPICodeGenAgent/v1 \
                                           -f task="Add reviews endpoint"
                                         # or:
                                         curl $AGENT_URL/run/agent ...
```

---

## CI/CD flow details

### deploy-agents.yml — when does it trigger?

Triggers on push to `main` when these paths change:
- `agents/**` — agent manifests updated (after genesis re-run)
- `workflows/**` — workflow definitions updated
- `context/**` — context docs updated (after genesis re-run)
- `deploy/Dockerfile` — base image changes

**Steps:**
1. OIDC auth to AWS (no long-lived keys)
2. Build Docker image from `deploy/Dockerfile`
3. Push to ECR (`acme-backend-agents:sha`, `acme-backend-agents:latest`)
4. Download current ECS task definition
5. Update task definition with new image digest
6. Deploy to ECS service (rolling update, circuit breaker enabled)
7. Wait for service stability (ECS health check passes)
8. Verify ALB `/health` returns 200

### agent-task.yml — how the two modes work

| Mode | When | How |
|------|------|-----|
| HTTP (preferred) | `AGENT_SERVICE_URL` variable is set | `curl POST $AGENT_URL/run/agent` |
| Local CLI (fallback) | `AGENT_SERVICE_URL` not set | `pip install` + `./ai run agent` |

HTTP mode is faster (no pip install step) and uses the always-on ECS service.
Local CLI mode works without any AWS setup — good for getting started.

---

## Security model

| Concern | How it's handled |
|---------|-----------------|
| API keys never in git | Stored in AWS Secrets Manager, injected at runtime by ECS |
| GitHub Actions AWS auth | OIDC (temporary creds via `sts:AssumeRoleWithWebIdentity`) |
| Agent HTTP access control | `AGENT_API_KEY` — Bearer token, set in Secrets Manager + GitHub Secret |
| Network isolation | ECS tasks in private subnets, ALB in public subnets, SG restricts port 8765 to ALB only |
| LLM output safety | RuleEngine enforces platform rules (non-negotiable, in rules.yaml) |
| Injection prevention | `shlex.quote()` on all shell args, `httpx` replaces curl for HTTP tools |

---

## Multi-team setup

If multiple domain teams use the same AWS account, namespace by domain:

```
# Each team gets:
ECR:              acme-backend-agents
                  payments-service-agents
                  data-platform-agents

ECS services:     acme-backend-agents
                  payments-service-agents

ALBs:             acme-backend-agents-alb
                  payments-service-agents-alb

Secrets Manager:  /gentcore/acme-backend/anthropic-api-key
                  /gentcore/payments-service/anthropic-api-key
```

Terraform `domain_name` variable drives the naming — each team runs `cdk apply` in their own repo with their own `domain_name`.
