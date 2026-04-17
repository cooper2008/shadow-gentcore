# Industry Packs — Generic Composition Pattern

## What this directory is

A home for **industry packs** — thin bundles that demonstrate how the framework
composes domain agents for any industry, known or unknown, without inventing
industry-specific agents.

**A pack is not a new agent type. It is a composition recipe**: a workflow
template + optional runbooks + optional capability binding overrides.
Everything else — the actual agent logic — comes from the shared stage
catalog (`_shared/*Agent/v*`).

## What problem packs solve

When a domain owner runs genesis on an unfamiliar industry, the framework
needs to produce a working roster without a human pre-authoring every
industry's agents. The composition pipeline handles this in 4 layers:

1. **Industry tag (B4)** — `domain.yaml` declares a free-form `industry:`
   string. The framework never rejects an unknown value.
2. **Capability map (B2)** — `config/capabilities.yaml` maps capabilities
   to toolpacks and declares stage defaults per stage. Unknown stages or
   capabilities return empty lists — callers fall back to SWE defaults.
3. **Industry pack (this directory, optional)** — when `industry:` matches
   a pack directory name, the pack's `capability_bindings.yaml` overrides
   stage_defaults and pack_preferences. When no pack exists, the
   framework proceeds with the shipped capability-map defaults.
4. **Architect v2 (B5)** — composes the roster from the `_shared/` stage
   catalog + the merged capability bindings. No new agent manifests are
   synthesised unless the catalog truly lacks a stage.

## Unknown-industry handling

**The framework never requires an industry pack to exist.** If a domain
declares `industry: medical-device-qa` and no `_shared/packs/medical-device-qa/`
directory ships, the pipeline:

1. Loads the industry tag (B4 — `Optional[str]`, any value accepted)
2. Queries `ManifestLoader.load_industries()` → missing → empty dict, no error
3. Queries `CapabilityResolver.resolve_capabilities_for_stage(name)` →
   falls back to the capability map's shipped stage defaults (B2)
4. Architect v2 composes from the shared stage catalog with sensible
   defaults; sets `decision: synthesize-new` for any stages not covered
   by the catalog and flags them for human review

The `harness/tests/test_unknown_industry_composition.py` suite verifies
each step of this fallback path.

## Pack shape

```
_shared/packs/<industry-name>/
  workflow_<process>.yaml    # DAG referencing _shared/*Agent stages
  capability_bindings.yaml   # industry-specific stage_overrides + pack_preferences
  runbooks/                  # B7 — frontmatter-tagged markdown
    <runbook-id>.md
```

All four layers are optional:
- A pack can be just `workflow_incident_response.yaml` (reuses shipped
  capability map + no runbooks).
- Or just `capability_bindings.yaml` (industry-specific tool preferences
  with no custom workflow).
- Or just a `runbooks/` directory (industry's documented procedures,
  retrieved via `toolpack://core/runbook_retrieval`).

## Worked example: aws-ops

`aws-ops/` ships as **one worked example** to prove the pattern works
end-to-end. It is **not** a built-in industry the framework depends on.
Contents:

- `workflow_incident_response.yaml` — Triage → Investigate → Execute
  (human-approval gate) → Summarize. References B1 stages; activates
  when prerequisites merge.
- `capability_bindings.yaml` — prefers `cloud/aws_advanced` over generic
  `cloud/aws` for the Execute stage; prefers PagerDuty for alerting.
- `runbooks/rds_failover.md` — a real AWS-centric runbook with YAML
  frontmatter (B7 format).

The same three files, with different tool packs and different runbooks,
would produce a k8s-ops pack, a healthcare-triage pack, or a
fintech-reconciliation pack — no framework changes required.

## Authoring a new pack

1. Pick your `<industry-name>` — kebab-case, matches `domain.yaml`'s
   `industry:` tag.
2. Create `_shared/packs/<industry-name>/capability_bindings.yaml` with
   `stage_overrides:` and optionally `pack_preferences:` sections.
3. (Optional) Add a `workflow_*.yaml` template that wires _shared/
   stages for your industry's canonical processes.
4. (Optional) Drop documented procedures in `runbooks/*.md` with
   frontmatter describing triggers and approval requirements.
5. Document in your `config/industries.yaml` entry (under
   `shadow-gentcore/config/industries.yaml`).

No new code. No new agents. The framework discovers your pack at
genesis time via the `industry:` tag and composes automatically.
