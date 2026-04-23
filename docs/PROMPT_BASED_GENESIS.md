# Prompt-based genesis

## What it is

Build a full domain (agents + workflows + standards) from a free-form description, **with zero source repos or docs**. The framework reads your `intent:` prompt, consults the curated industry library (`config/best_practices/<industry>.yaml`), and synthesizes everything a normal genesis run would produce — same 11-step pipeline, same output shape.

Use this when:

- You're sketching a new domain and don't have a codebase yet.
- You want reasonable defaults for an industry without committing sources upfront.
- You're running an exploratory genesis to see what a fleet *would* look like before building it.

You can always re-run with `reference:`/`target:`/`docs:` added to `domain.yaml` later to refine.

## Quick start

```bash
mkdir -p /tmp/prompt-only && cat > /tmp/prompt-only/domain.yaml <<'EOF'
name: prompt-only
industry: backend
intent: |
  Backend service for a fintech platform: FastAPI + async SQLAlchemy,
  PostgreSQL, regulated under SOC 2. Needs audit logging, PII redaction
  in logs, and request tracing. No existing codebase yet.
EOF

mkdir -p /tmp/prompt-only/config && cat > /tmp/prompt-only/config/provider.yaml <<'EOF'
provider: anthropic
model: claude-sonnet-4-6
max_tokens: 32768
api_key_env: ANTHROPIC_API_KEY
EOF

./ai run workflow workflows/genesis/genesis_prompt.yaml --domain /tmp/prompt-only --yes
```

Output will include:

- `agents/*` — domain agents the architect designed based on the intent
- `workflows/*.yaml` — workflows the architect produced
- `context/standards.md` — Tier 1, derived from the curated library + intent
- `context/best_practices.md` — Tier 1.5 overlay (gap analysis against library baseline)
- `context/reference/chunks/*.md` — Tier 2 reference docs

## How it's wired

`workflows/genesis/genesis_prompt.yaml` substitutes `BestPracticeResearchAgent` for the usual `scan → map` pair:

```
research → resolve → {discover_tools, engineer_context}
                   → verify → advise → synthesize_tools
                   → architect → build → validate
```

The research agent:

- Reads your `intent` from `task_input.team_config.intent` (populated by the CLI from `domain.yaml`).
- Gets the industry library pre-loaded via the `best_practice_library` context preload.
- Emits the same `knowledge_map + coverage + gaps` shape `KnowledgeMapperAgent` would emit, so every downstream step (resolve, engineer_context, architect, build) works unchanged.

Gate thresholds are slightly relaxed vs `genesis_build.yaml` — `research_gate` accepts `coverage.overall >= 30` (vs 40) since the library has fewer signal sources than a rich repo scan.

## What makes a good `intent:`

The research agent is pretty forgiving but its output quality tracks directly with prompt specificity. Good prompts describe:

1. **System type** — API service, batch ETL, SPA, mobile, CLI.
2. **Stack hints** — language / framework / DB / deployment target.
3. **Regulatory posture** — SOC 2 / HIPAA / PCI / GDPR / "regulated".
4. **Operational shape** — single-tenant, multi-tenant, on-prem, SaaS.
5. **Scale hints** — "low traffic" vs "millions of requests".
6. **Explicit concerns** — audit logging, PII, rate limiting, multi-region.

Thin prompts (`intent: "backend service"`) still work but the research agent will score coverage low and the architect will produce a generic fleet. The CLI surfaces a warning when `intent` is shorter than 20 characters.

## Choosing an industry

Each `config/best_practices/<industry>.yaml` drives research. Ship today:

- `backend` — FastAPI + SQLAlchemy + PostgreSQL baseline (10 principles)
- `frontend` — React + TypeScript + SPA baseline (7 principles)
- `data` — Spark/dbt/Airflow data-platform baseline (7 principles)

If the industry you need isn't shipping, copy `config/best_practices/_schema.yaml` to `<yours>.yaml` and author it. Next run with `industry: <yours>` picks it up — no code changes. See [BEST_PRACTICES_OVERLAY.md](BEST_PRACTICES_OVERLAY.md) for authoring guidance.

## Combining prompt + sources

You don't have to choose. Provide both — `intent` plus `reference:`/`target:`/`docs:` — and genesis_build.yaml will use your prompt as a scanning hint while still extracting everything it can from your code. The research agent doesn't fire in that mode; SourceScanner + KnowledgeMapper do the work as usual.

Use `genesis_prompt.yaml` only when you truly have no sources. When sources exist, stick with `genesis_build.yaml`.

## Verifying the run

```bash
# Does the overlay file exist?
ls /tmp/prompt-only/context/best_practices.md

# Did the fleet end up reasonable for the intent?
cat /tmp/prompt-only/agents/*/v1/agent_manifest.yaml | grep -E '^id:|^description:'

# Does a generated agent see both tiers at runtime?
./ai run agent prompt-only/TriageAgent/v1 \
    --task "investigate latency on /orders" \
    --domain /tmp/prompt-only \
    --dry-run 2>&1 | grep -E 'preload:|standards|best_practices'
```

## Limits

- **Library coverage bound**: research output is only as deep as the library. Adding principles to the library → richer research output next run.
- **No tool-pack discovery from prompt**: `discover_tools` still runs but with minimal signal — tool packs come from the `agent-tools` catalog, not the prompt. Prompts that mention specific tools (Jira, Datadog, Slack) help it pick the right packs.
- **No custom-fit standards**: your `standards.md` will read as "library-derived" until you re-run with real sources. That's fine for bootstrap; re-run genesis_build.yaml when you have code to ground it in.
