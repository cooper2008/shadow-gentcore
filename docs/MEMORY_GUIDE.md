# Memory Architecture — 5 Tiers

When a domain agent needs information, it walks a cheap-to-expensive ladder
before saying "I don't have that." This doc explains the ladder, why it
beats monolithic RAG for this framework, and how to configure per-agent
memory declaratively.

## The ladder

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │ Tier 1 — standards.md  (always-injected, ≤500 lines, token-free)    │
 └─────────────────────────────────────────────────────────────────────┘
                                    │ miss
                                    ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ Tier 2 — context_retrieve(topic, keywords, top_k=3)                  │
 │   keyword-indexed chunks from reference_index.yaml                   │
 │   ~5 ms, ~500 tokens, deterministic, no embeddings                   │
 └─────────────────────────────────────────────────────────────────────┘
                                    │ miss
                                    ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ Tier 3 — origin_fetch(path)                                          │
 │   live re-fetch from SourceAdapter cache (github://, file://, …)     │
 │   scope-guarded glob, audit-logged to .gentcore/origin_log.jsonl     │
 │   cached first, re-downloads only on cache miss                      │
 └─────────────────────────────────────────────────────────────────────┘
                                    │ miss
                                    ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ Tier 4 — memory_recall(key, k=5)                                     │
 │   FileMemoryStore JSONL, per-agent, keyword-searched                 │
 │   AgentRunner auto-records run_output on success                     │
 └─────────────────────────────────────────────────────────────────────┘
                                    │ miss
                                    ▼
                   "I don't have that information."

 ┌─────────────────────────────────────────────────────────────────────┐
 │ Tier 5 — EvolutionAgent (async, weekly)                              │
 │   Reads origin_log + memory_store, proposes standards.md / chunk     │
 │   updates when agents keep hitting the same gaps.                    │
 │   Human-approval gate before any edit applies.                       │
 └─────────────────────────────────────────────────────────────────────┘
```

## Why keyword-indexed instead of embeddings

We deliberately skip embeddings + vector DB. Tradeoffs for this framework:

| Concern | Traditional RAG | Tiered approach |
|---|---|---|
| Infrastructure | Pinecone / Weaviate / pgvector | YAML + filesystem + SQLite |
| Query latency | Embedding → ANN → rerank (~200 ms) | Keyword scan (~5 ms) |
| Freshness | Re-embed on every change | Tier 1-2 snapshot; Tier 3 live |
| Debugging | "Why did it retrieve X?" opaque | Score breakdown: topic match + keyword overlap + inverse-rarity |
| Failure | Silently returns irrelevant chunks | Miss at Tier 2 falls through to Tier 3 origin re-fetch |

For domain-specific agents (not open-web search) the knowledge base is small, keyword-bounded, and well-categorized — embeddings add cost without improving precision.

## On-disk shape (per domain)

```
<domain>/
  context/
    standards.md                          ← Tier 1
    glossary.md
    reference_index.yaml                  ← Tier 2 SEARCHABLE INDEX
    reference/
      chunks/
        fastapi_routers.md                ← chunk body (≤5 KB)
        db_models.md
        pytest_async.md
      # (legacy monolithic files still supported but de-prioritized)
  .gentcore/
    memory/<agent_short>/
      memories.jsonl                      ← Tier 4 per-agent memory
    origin_log.jsonl                      ← Tier 3 audit trail
```

`reference_index.yaml`:

```yaml
version: 1
chunks:
  - id: fastapi_routers
    topic: "FastAPI router patterns"
    keywords: [router, APIRouter, prefix, tag, include_router]
    summary: "How routers organize endpoints and compose into the app."
    path: reference/chunks/fastapi_routers.md
    size_bytes: 2400
```

## How to use each tier — generated domain agent

### Declare tools in the agent manifest

```yaml
# agents/MyAgent/v1/agent_manifest.yaml
tools:
  - name: context_retrieve     # Tier 2 (cheap)
  - name: origin_fetch          # Tier 3 (network, logged)
  - name: memory_recall         # Tier 4 (past solutions)

origin_fallback:                # optional — Tier 3 scope + source defaults
  sources:
    - github://acme/backend@main
  scope: "src/**"               # glob bounding what paths can be fetched
```

### Teach the decision ladder in the system prompt

```markdown
When you need reference info:
  1. **Tier 1**: Check standards.md (always in your context) first.
  2. **Tier 2**: If standards doesn't cover it, call
     `context_retrieve(topic="X", keywords=["Y", "Z"])` — returns the
     3 most relevant chunks.
  3. **Tier 3**: If Tier 2 returns nothing useful, call
     `origin_fetch(path="src/auth/session.py")` — reads directly from
     the origin repo via cache. Scope-guarded.
  4. **Tier 4**: For task-specific solutions you may have seen before,
     call `memory_recall(key="run_output", k=3)` — past runs of this agent.
  5. Only AFTER step 4 comes up empty should you say "I don't have that."
```

## Tier 2 — context_retrieve

### Scoring

```
score = topic substring match (+2.0)
      + topic word overlap    (+1.0 per word)
      + keyword match         (+ 1/log(1+df), inverse-rarity weighted)
      + partial keyword (in topic/summary) (+0.3)
      × 0.9 if chunk > 5 KB (size penalty)
```

Returns top-K above `min_score=0.5` (default). All ranking math is in
`harness/core/context_retriever.py` — inspect it if a chunk ranks oddly.

### Input / output

```python
await executor.execute({
    "name": "context_retrieve",
    "arguments": {
        "domain_root": "/path/to/domain",
        "topic": "router patterns",
        "keywords": ["prefix", "APIRouter"],
        "top_k": 3,
        "min_score": 0.5,
    },
})
# → stdout: rendered chunk bodies with score headers
#   hit: bool, chunks_returned: N
```

### When Tier 2 produces empty results

The formatter emits a **fallback hint pointing at Tier 3**:

> `[context_retrieve] No reference chunks matched topic='X' keywords=['Y'].
> Tier 3 origin_fetch may have what you need; otherwise proceed from standards.md.`

This is how the LLM knows to try `origin_fetch` next.

## Tier 3 — origin_fetch

### Scope guard

```yaml
# agent_manifest.yaml
origin_fallback:
  scope: "src/**"    # only paths matching this glob are fetchable
```

Paths outside scope return `scope_rejected: true` WITHOUT any network
request. Stops runaway agents from reading arbitrary files.

### Audit log

Every call appends to `<domain>/.gentcore/origin_log.jsonl`:

```json
{"timestamp": 1776856998.69, "path": "src/auth/session.py",
 "source_uri": "github://acme/backend@main", "outcome": "ok",
 "bytes": 2300, "elapsed_ms": 180}
```

EvolutionAgent (Tier 5) reads this log — when agent X keeps fetching
`src/auth/session.py`, it's a signal that standards.md or chunks are
missing content, and the Evolution prompt proposes an update.

### Rate limits

Not enforced in-code yet. The SourceAdapter cache absorbs repeat calls
for free; a future rate-limit store will honour
`origin_fallback.rate_limit: 10_per_hour` declarations without touching
the adapter.

## Tier 4 — memory_recall

### What gets recorded

AgentRunner auto-records `key="run_output"` entries after every completed
run when a memory_store is wired in. The store IS wired by default in
production — `manifest_loader.build_memory_store(domain_root)` is called
from `boot_engine`, `server/runner.py`, and `./ai run agent` so every
execution path writes to:

```
<domain_root>/.gentcore/memory/<agent>/memories.jsonl
```

Set `GENTCORE_MEMORY_DIR` to relocate the store (e.g. to share a pool
across domains for cross-domain genesis memory):

```bash
GENTCORE_MEMORY_DIR=~/.gentcore/genesis_memory ./ai genesis build --domain …
```

Resolution order: `memory_root` argument → `GENTCORE_MEMORY_DIR` env →
`<domain_root>/.gentcore/memory`.

### What the agent can query

```python
await executor.execute({
    "name": "memory_recall",
    "arguments": {"agent_id": "acme/TriageAgent/v1", "key": "run_output", "k": 5},
})
# → stdout: past entries newest-first, with age labels
```

AgentRunner also auto-injects the last 3 `run_output` entries into each
new invocation's context — an agent doesn't have to call `memory_recall`
to get short-term continuity, only to reach further back.

Empty recall returns a friendly "This may be the agent's first run" hint
— not an error.

### Retention

`FileMemoryStore` has `max_entries` (default 100) + optional
`max_age_seconds`. Oldest entries evict on each `store()` call.

## Tier 5 — EvolutionAgent

`harness/core/evolution_signals.py::gather_evolution_signals()` aggregates
three runtime audit trails into a structured bundle:

- `<domain>/.gentcore/origin_log.jsonl` → `OriginHotspot` list (paths
  re-fetched ≥2×, ranked by `count × 30-day recency half-life ×
  not-found penalty`)
- Memory stores across agents → `MemoryPattern` list (signatures seen
  ≥3×, template candidates)
- Supplied run records with `_citation_report` → `CitationWeakness`
  list (agents whose avg `citation_score` fell below 0.75 over ≥3 runs,
  with which tiers were most under-cited)

The bundle is injected into `EvolutionAgent/v1` via the `context.preload:
[domain_evolution_signals]` source (wired in
`manifest_loader._build_preload_item`). The agent runs single-shot in
`chain_of_thought` mode with `tools: []` — no iterative file_read needed.

Workflow: `workflows/genesis/genesis_evolve.yaml` wires
EvolutionAgent → AgentBuilderAgent (apply) → QualityGateAgent
(revalidate) with a `revalidate_to_apply` feedback loop.

Zero LLM calls in the aggregator itself — deterministic, cheap, safe to
schedule on a cron.

## Tuning per agent

Not every agent needs every tier:

| Agent shape | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|---|---|---|---|---|
| Lightweight notification (Slack ping) | ✓ | — | — | — |
| Code reviewer | ✓ | ✓ | ✓ | — |
| Long-running analyst | ✓ | ✓ | ✓ | ✓ |
| Incident triage | ✓ | ✓ | ✓ | ✓ |
| Content summarizer | ✓ | ✓ | — | ✓ |

Architect v2 makes this decision per-agent by inspecting the roster
entry's task shape and stamping the right tools into the manifest.

## Debugging memory calls

### Why did / didn't a chunk get returned?

```bash
.venv/bin/python -c "
from harness.core.context_retriever import ContextRetriever
r = ContextRetriever.for_domain('/path/to/domain')
result = r.search(topic='router', keywords=['prefix'], top_k=5, min_score=0.0)
for chunk, score in result.chunks:
    print(f'{score:.2f}  {chunk.id}  kws={chunk.keywords}')
"
```

### What has the agent been fetching from origin?

```bash
cat <domain>/.gentcore/origin_log.jsonl | jq '.path' | sort | uniq -c | sort -rn
# top-5 most-fetched paths → candidates for standards.md expansion
```

### What does the agent remember?

```bash
cat .gentcore/memory/<agent>/memories.jsonl | jq -r '"\(.timestamp) \(.key) \(.value[:80])"'
```

## Integration with contract validator

`./ai validate-contracts --domain <path>` enforces memory-tier
consistency at static-check time:

- If prompt teaches `context_retrieve(...)` but manifest doesn't declare
  `context_retrieve` in tools → WARN
- Same for `origin_fetch` and `memory_recall`

Run this in CI to catch drift before it hits production agents.

## Summary

The memory ladder is **cheap before expensive**, **snapshot before live**,
**general before specific**. Each tier falls through to the next with a
hint, so the LLM always knows what to try next instead of hallucinating
a gap-fill.
