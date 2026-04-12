# Domain Certification Guide

How to certify a domain before publishing it to the catalog.

## What is Certification?

Certification is a multi-step quality gate that confirms a domain is:
1. **Valid** — all manifests parse and pass structural validation
2. **Runnable** — the domain passes a dry-run execution
3. **Graded** (optional) — sample outputs meet the evaluator score threshold
4. **Observable** (optional) — the domain logs structured events

## Certification Steps

### Step 1: Validation

The `Certifier` runs `Validator.validate_domain()` internally. A domain must pass with zero errors.

Key checks:
- `domain.yaml` has `name`, `owner`, `purpose`
- Each agent's `system_prompt_ref` file exists on disk
- Workflow DAGs have no cyclic dependencies
- Port `depends_on` references resolve to known steps

### Step 2: Dry Run

A stub execution confirms the domain's workflow topology is sound without making real LLM calls.

Default dry run: no-op success (pass).

Custom dry run:
```python
from harness.authoring.certifier import Certifier

def my_dry_run(domain_path):
    # Execute with replay fixtures
    return {"success": True}

certifier = Certifier()
result = certifier.certify_domain(domain_path, dry_run_fn=my_dry_run)
```

If `dry_run_fn` returns `{"success": False}`, certification fails at this step.

### Step 3: Evaluator Threshold (Optional)

Configure a minimum grading score for sample outputs:
```python
result = certifier.certify_domain(
    domain_path,
    evaluator_threshold=0.8,
    sample_outputs=[{"output": "...", "criteria": ["...", "..."]}],
)
```

### Step 4: Observability Compliance (Optional)

Verify the domain logs structured events using `StructuredLogger`:
```python
result = certifier.certify_domain(domain_path, require_observability=True)
```

## Reading Results

```python
result = certifier.certify_domain(domain_path)
print(result.summary)        # "CERTIFIED: ..." or "NOT CERTIFIED: ..."
print(result.certified)      # True/False
print(result.dry_run_passed) # True/False
for note in result.notes:
    print(note)
```

## CLI

```bash
./ai certify ./domains/my_domain
# CERTIFIED: dry_run=PASS validation=PASS
```

Exit code 0 = certified, 1 = not certified.

## CI Integration

Certify during CI before merging:

```yaml
# .github/workflows/certify.yml
- name: Certify domain
  run: ./ai certify ./domains/my_domain
```

## Re-certification After Changes

Any change to:
- `domain.yaml` ports, policies, or version
- Agent manifests (category, mode, tools)
- Workflow topology (steps, dependencies, gates)

...requires re-running certification before re-publishing.
