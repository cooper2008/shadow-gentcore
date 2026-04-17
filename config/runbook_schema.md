# Runbook Schema (B7)

Runbooks are first-class domain knowledge under `context/runbooks/`. Every runbook
is a **plain Markdown file** with an optional YAML frontmatter block carrying
structured metadata. Generic stage agents (Triage, Investigate, Execute,
Respond, Retrieve) consume runbooks to make their behaviour domain-specific
without bespoke code.

## File location

Runbooks live under the domain's context:

```
<domain>/
  context/
    runbooks/
      rds_multi_az_failover.md
      ecs_rollback.md
      incident_triage_playbook.md
    standards.md
    architecture.md
```

During genesis, `ContextEngineerAgent` copies or symlinks runbook sources from
`docs:` references in `domain.yaml` into `context/runbooks/`.

## Frontmatter fields (all optional)

| Field | Type | Purpose |
|-------|------|---------|
| `id` | string | Stable identifier. If absent, defaults to the filename stem. |
| `triggers` | list&lt;string&gt; | Machine-matchable signal tags (e.g. `rds_instance_unhealthy`). TriageAgent uses these to route a signal to the right runbook. |
| `estimated_duration` | string | Human-readable ETA (e.g. `15m`, `2h`). Helps Execute/Respond gate on blast radius. |
| `blast_radius` | string | One of `single_resource`, `single_service`, `single_db`, `single_region`, `multi_region`, `fleet`. Higher radius → require explicit approval. |
| `approval_required` | bool | When true, ExecuteAgent MUST wait for a human approval gate before running. |
| `tags` | list&lt;string&gt; | Free-form classification (`database`, `rds`, `networking`, …) |
| `related_runbooks` | list&lt;string&gt; | IDs of linked runbooks the agent may need to reference |

## Example

```markdown
---
id: rds-multi-az-failover
triggers: [rds_instance_unhealthy, rds_failover_required]
estimated_duration: 15m
blast_radius: single_db
approval_required: true
tags: [database, rds, high_availability]
related_runbooks: [rds-restore-snapshot, postgres-replica-lag]
---

# RDS Multi-AZ Failover Runbook

## Preconditions
- RDS instance has Multi-AZ enabled
- You have the `rds:RebootDBInstance` IAM permission
- On-call has been notified via PagerDuty

## Steps

1. Verify replica lag is acceptable:
   ```
   aws rds describe-db-instances --db-instance-identifier <id>
   ```
2. Promote the standby:
   ```
   aws rds reboot-db-instance --db-instance-identifier <id> --force-failover
   ```
3. Wait for status `available`, then verify client connectivity.

## Rollback
No direct rollback — failover is one-way. If the new primary is unhealthy,
restore from the most recent snapshot (see `rds-restore-snapshot.md`).

## References
- [AWS docs: Failover](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html)
```

## Agent integration

- **TriageAgent** matches inbound signal tags to `triggers:` to pick the correct runbook.
- **ExecuteAgent** reads the file body as its step list. Respects `approval_required`.
- **RespondAgent** references `related_runbooks` when diagnosing root causes.
- **RetrieveAgent** provides RAG-style search across all runbooks via the
  `toolpack://core/runbook_retrieval` toolpack.

## Retrieval toolpack

Domains that declare `toolpack://core/runbook_retrieval` in an agent's `tools:`
list get two tools:

- `runbook_search` — free-text query; returns ranked excerpts with source paths.
- `runbook_load` — load a specific runbook by id.

Both tools use the `RunbookLibrary` in `harness/core/runbook_loader.py`.
