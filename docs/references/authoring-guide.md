# Authoring Guide

How to scaffold, validate, certify, and publish domains using the `shadow-gentcore` authoring kit.

## Overview

The authoring kit lives in `harness/authoring/` and is exposed via the `./ai` CLI:

```
./ai domain init <name>        # Scaffold a new domain
./ai agent create <domain> <name>    # Scaffold an agent
./ai workflow create <domain> <name> # Scaffold a workflow
./ai pack create <domain> <name>     # Scaffold a tool pack
./ai validate <path>           # Validate manifests at path
./ai certify <path>            # Certify a domain
./ai publish <path>            # Publish to local catalog
```

## 1. Scaffold a Domain

```bash
./ai domain init my_domain --owner platform --path ./domains
```

Creates:
```
domains/my_domain/
  domain.yaml          # Domain manifest
  agents/              # Agent bundle directory
  workflows/           # Workflow definitions
```

`domain.yaml` minimum required fields:
```yaml
name: my_domain
owner: platform
purpose: "Brief description of the domain"
```

## 2. Scaffold an Agent

```bash
./ai agent create ./domains/my_domain MyAgent --category code_generation
```

Creates:
```
domains/my_domain/agents/MyAgent/v1/
  agent_manifest.yaml  # Agent manifest
  system_prompt.md     # System prompt content
```

Key `agent_manifest.yaml` fields:
```yaml
id: "my_domain/MyAgent/v1"
name: MyAgent
domain: my_domain
version: v1
category: code_generation
system_prompt_ref: "agents/MyAgent/v1/system_prompt.md"
execution_mode: react
```

## 3. Scaffold a Workflow

```bash
./ai workflow create ./domains/my_domain ci_pipeline
```

Creates `workflows/ci_pipeline.yaml` with a stub step sequence.

Workflow topology rules:
- Each step must have a unique `name`
- `depends_on` references must point to existing step names
- No circular dependencies

## 4. Validate

```bash
./ai validate ./domains/my_domain
```

The `Validator` checks:
- `domain.yaml` exists and has required fields (`name`, `owner`, `purpose`)
- All agent manifests have required fields and `system_prompt_ref` exists on disk
- Workflow DAGs have no cyclic dependencies
- Port references are resolvable

Exit code 0 = valid, 1 = errors found.

## 5. Certify

```bash
./ai certify ./domains/my_domain
```

Certification steps:
1. **Validation** — full `Validator` pass
2. **Dry run** — stub execution of the domain (or custom `dry_run_fn`)
3. **Evaluator threshold** (optional) — grading score must meet minimum
4. **Observability compliance** (optional) — structured logging must be enabled

## 6. Publish

```bash
./ai publish ./domains/my_domain --version 1.0.0 --owner platform
```

Writes a catalog entry to `~/.agent-catalog/` (default) or configured catalog dir.

Discover published domains:
```python
from harness.authoring.publisher import Publisher
pub = Publisher()
entries = pub.discover("my_domain")
latest = pub.get_latest_version("my_domain")
```

## Programmatic API

```python
from pathlib import Path
from harness.authoring.scaffolder import Scaffolder
from harness.authoring.validator import Validator
from harness.authoring.certifier import Certifier
from harness.authoring.publisher import Publisher

s = Scaffolder()
s.scaffold_domain("my_domain", Path("./domains"), owner="platform")

v = Validator()
result = v.validate_domain(Path("./domains/my_domain"))
assert result.is_valid, result.summary

c = Certifier()
cert = c.certify_domain(Path("./domains/my_domain"))
assert cert.certified

pub = Publisher()
pub.publish(Path("./domains/my_domain"), version="1.0.0", owner="platform")
```
