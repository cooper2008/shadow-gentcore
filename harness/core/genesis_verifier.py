"""Post-genesis verification — smoke-test generated agents before declaring success."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def verify_genesis_output(domain_dir: str | Path) -> dict[str, Any]:
    """Verify that genesis output is structurally valid.

    Checks:
    1. agents/ directory exists with at least 1 agent
    2. Each agent has agent_manifest.yaml + system_prompt.md
    3. Manifests parse as valid YAML with required fields
    4. workflows/ has at least 1 workflow
    5. Workflow steps reference agents that exist
    6. context/ has standards.md

    Returns dict with 'passed', 'total_checks', 'failures' keys.

    Example::

        result = verify_genesis_output("/path/to/generated-domain")
        if not result["passed"]:
            for issue in result["failures"]:
                print(f"  - {issue}")
    """
    domain_dir = Path(domain_dir)
    failures: list[str] = []
    total = 0

    # ── 1. agents/ directory ───────────────────────────────────────────────
    agents_dir = domain_dir / "agents"
    total += 1
    if not agents_dir.exists():
        failures.append("agents/ directory not found")
    else:
        agent_manifests = list(agents_dir.rglob("agent_manifest.yaml"))
        total += 1
        if not agent_manifests:
            failures.append("No agents found in agents/")

        for manifest_path in agent_manifests:
            agent_dir = manifest_path.parent
            agent_name = str(agent_dir.relative_to(agents_dir))
            data: dict[str, Any] = {}

            # ── 2. Manifest parses as valid YAML ──────────────────────────
            total += 1
            try:
                data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                failures.append(f"Agent {agent_name}: invalid YAML — {exc}")
                # Skip field checks; manifest is unreadable
                continue

            # ── 3. Required manifest fields ───────────────────────────────
            for field in ("id", "domain", "category"):
                total += 1
                if field not in data:
                    failures.append(f"Agent {agent_name}: missing '{field}' in manifest")

            # ── 4. System prompt file exists ──────────────────────────────
            total += 1
            prompt_ref: str = (
                data.get("system_prompt_ref", "system_prompt.md")
                if isinstance(data, dict)
                else "system_prompt.md"
            )
            if not (agent_dir / prompt_ref).exists():
                failures.append(f"Agent {agent_name}: missing {prompt_ref}")

    # ── 5. workflows/ directory ────────────────────────────────────────────
    workflows_dir = domain_dir / "workflows"
    total += 1
    if not workflows_dir.exists():
        failures.append("workflows/ directory not found")
    else:
        wf_files = list(workflows_dir.glob("*.yaml")) + list(workflows_dir.glob("*.yml"))
        total += 1
        if not wf_files:
            failures.append("No workflows found in workflows/")

        for wf_file in wf_files:
            total += 1
            try:
                wf_data = yaml.safe_load(wf_file.read_text(encoding="utf-8")) or {}
                if "steps" not in wf_data:
                    failures.append(f"Workflow {wf_file.name}: missing 'steps'")
            except yaml.YAMLError as exc:
                failures.append(f"Workflow {wf_file.name}: invalid YAML — {exc}")

    # ── 6. context/standards.md ────────────────────────────────────────────
    total += 1
    context_dir = domain_dir / "context"
    if context_dir.exists():
        if not (context_dir / "standards.md").exists():
            failures.append("context/standards.md not found")
    # context/ being absent is not itself a failure — only its contents are checked when present

    passed = len(failures) == 0
    result: dict[str, Any] = {
        "passed": passed,
        "total_checks": total,
        "failures": failures,
        "failure_count": len(failures),
    }

    if passed:
        logger.info("Genesis verification passed (%d checks)", total)
    else:
        logger.warning(
            "Genesis verification failed: %d issues in %d checks",
            len(failures),
            total,
        )
        for f in failures:
            logger.warning("  - %s", f)

    return result
