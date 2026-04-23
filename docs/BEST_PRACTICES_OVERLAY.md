# Best-practice overlay (Tier 1.5)

## What it is

Every genesis run produces a `context/standards.md` (Tier 1) that reflects what the scanner found in your sources. That's **your** standards — grounded in your actual code and docs.

The overlay (`context/best_practices.md`) is the companion document that catches what your standards *don't* cover. It's a GAP analysis against a curated industry baseline — "you're missing structured logging with request correlation" — *not* a rewrite of your standards. Both files are always-injected into every domain agent at runtime.

Think of it as: **standards.md = what we do** / **best_practices.md = what we should also do**.

## Quick example

You genesis a backend service whose README never mentions logging. Scanner + ContextEngineer emit `standards.md` with everything they could extract (async SQLAlchemy, pytest setup, migration conventions). They can't emit what isn't in your sources.

BestPracticeAdvisorAgent then compares `standards.md` against `config/best_practices/backend.yaml` (which ships with 10 principles), finds `structured_logging` isn't covered, and writes:

```markdown
## Critical gaps (severity: must)

### `structured_logging` — Structured logging with request correlation

**Why this matters:** Unstructured logs are unusable at any non-trivial traffic level. A single correlation ID threaded through every log line for a request is the difference between "3-minute root-cause analysis" and "3-hour grep session."

**What's missing from your standards:**
- Every log line emits request_id (or trace_id)
- JSON output format in production
- Secrets redacted before logging

**Concrete suggestion:** Add `request_id` propagation via `contextvars` + a `JsonFormatter`. See chunk `fastapi_middleware` for an implementation recipe.

**References:** https://12factor.net/logs, https://opentelemetry.io/docs/concepts/signals/logs/
```

When your TriageAgent / CodeWriterAgent / ReviewerAgent run, they see both documents — so they'll add logging correctly even though your standards don't require it explicitly.

## How it works end-to-end

```
┌─────────────────┐   ┌───────────────┐   ┌──────────────────────┐
│  standards.md   │   │ config/       │   │ best_practices.md    │
│  (your code-    │   │ best_         │   │ (gap analysis,       │
│  grounded       │ + │ practices/    │ ─▶│  ≤300 lines)         │
│  standards)     │   │ <industry>    │   │                      │
└─────────────────┘   │ .yaml         │   └──────────────────────┘
        │             └───────────────┘            │
        │                                          │
        ▼                                          ▼
 Tier 1 preload                           Tier 1.5 preload
 (priority 10)                            (priority 9)
        │                                          │
        └──────────────┬───────────────────────────┘
                       ▼
       Every domain agent sees BOTH at runtime
```

Key properties:

- **Additive**: standards.md is never modified. If you hand-edit it, the overlay gets regenerated on the next genesis run but your standards stay put.
- **Opt-out safe**: the preload returns `None` when `context/best_practices.md` is absent — existing domains that haven't been re-genesis'd continue to work with just standards.md.
- **Bounded**: overlay capped at 300 lines so Tier 1 + 1.5 combined still fit a reasonable token budget.

## The libraries

Ship today: `config/best_practices/backend.yaml`, `frontend.yaml`, `data.yaml`. Each carries 7-10 authored principles with severity (must/should/nice), concrete `must_have` markers, anti-patterns, and authoritative URLs.

To add a new industry, copy `config/best_practices/_schema.yaml` to `<yours>.yaml` and fill it in. Next genesis run with `industry: <yours>` in `domain.yaml` picks it up automatically — no code changes.

To extend an existing industry, just append principles to the file. They'll take effect on the next genesis run.

## Authoring principles

```yaml
principles:
  - id: structured_logging           # stable snake_case — surfaces in gap reports
    title: Structured logging with request correlation
    severity: must                   # must | should | nice
    why: >-                          # quoted verbatim in gap reports; write for the domain team, not library authors
      Unstructured logs are unusable at scale; ...
    must_have:
      - "request_id in every log line"
      - "JSON output format in production"
    anti_patterns:
      - "print() statements for logging"
    related_chunks:                  # optional — Tier 2 chunk IDs with implementation recipes
      - "fastapi_middleware"
    source_urls:
      - "https://12factor.net/logs"
```

Guidelines:

1. **Keep principles focused** — one concept per entry. Split "logging + tracing + metrics" into three.
2. **Lead the `why` with the cost of not doing it** — makes the advisor's gap report actionable ("you're missing X, which means Y will bite you").
3. **Cite authoritative sources** — specs, RFCs, docs. Blog posts go stale.
4. **Severity discipline** — `must` is for things that routinely cause outages or breaches. Everything else is `should` or `nice`.
5. **Under 200 principles per file** — beyond that the advisor can't keep them all in context at typical LLM windows.

## Runtime inspection

```bash
# See what the library looks like rendered for the advisor
python3 -c "
from pathlib import Path
from harness.core.manifest_loader import _build_preload_item
tmp = Path('/tmp/probe'); tmp.mkdir(exist_ok=True)
(tmp / 'domain.yaml').write_text('name: probe\nindustry: backend\n')
print(_build_preload_item('best_practice_library', tmp)['content'])
"

# See the overlay file a genesis run produced
cat /path/to/your-domain/context/best_practices.md
```

## When the overlay doesn't fire

- **No `industry:` in domain.yaml** — advisor's library preload returns None, the agent produces a one-paragraph "library not found" stub, and the file is still written so the preload stays consistent.
- **No library for that industry** — same behaviour. Ship a library file to unblock.
- **Gate-fail** — `advise_gate` is permissive (`status == success`, `on_fail: degrade`), so a hard error in the advisor doesn't block the pipeline; you'll see `advise: degraded` in the CLI output. Inspect the step's `error` field to diagnose.

## Extending per-agent

Generated agent manifests include `context.preload: [best_practices_overlay]` by default. If you want an agent to receive the overlay alongside other preloads, just add it to the list:

```yaml
context:
  preload:
    - best_practices_overlay
    - domain_context_docs
```

Existing agents (generated before the overlay landed) can adopt it with the same one-line edit. Priority 9 puts it between always-injected standards (10) and Tier 2 reference chunks (5).
