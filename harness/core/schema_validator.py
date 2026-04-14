"""Validate agent manifests and workflow YAMLs against expected schemas.

Sub-second static checks — no LLM calls, no agent execution.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Required fields for agent_manifest.yaml
_AGENT_REQUIRED: frozenset[str] = frozenset({"id", "domain", "category", "description"})
_AGENT_OPTIONAL: frozenset[str] = frozenset({
    "version", "execution_mode", "tools", "constraints", "permissions",
    "input_schema", "output_schema", "grading_criteria_ref", "system_prompt_ref",
    "hooks_ref", "metadata", "pack",
})

# Required fields for workflow YAML
_WORKFLOW_REQUIRED: frozenset[str] = frozenset({"name", "steps"})
_STEP_REQUIRED: frozenset[str] = frozenset({"name", "agent"})

# Valid execution modes
_VALID_MODES: frozenset[str] = frozenset({"react", "chain_of_thought", "plan_execute", "direct"})

# Valid gate on_fail values
_VALID_ON_FAIL: frozenset[str] = frozenset({
    "retry", "retry_fresh", "rollback", "abort", "escalate_human", "degrade", "fallback",
})

# Valid gate types
_VALID_GATE_TYPES: frozenset[str] = frozenset({"standard", "router", "approval"})


def validate_agent(agent_dir: str | Path) -> list[str]:
    """Validate an agent directory.

    Checks that the directory contains a valid ``agent_manifest.yaml`` and all
    referenced side-car files exist.

    Args:
        agent_dir: Path to an agent bundle directory (e.g. ``agents/MyAgent/v1``).

    Returns:
        A list of human-readable issue strings.  Empty list means the agent is valid.

    Example::

        issues = validate_agent("agents/CodeReviewAgent/v1")
        if issues:
            for msg in issues:
                print(f"  - {msg}")
    """
    issues: list[str] = []
    agent_dir = Path(agent_dir)

    if not agent_dir.is_dir():
        return [f"Agent directory not found: {agent_dir}"]

    manifest_path = agent_dir / "agent_manifest.yaml"
    if not manifest_path.exists():
        issues.append("Missing agent_manifest.yaml")
        return issues

    try:
        data: dict[str, Any] = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        issues.append(f"Invalid YAML in manifest: {exc}")
        return issues

    if not isinstance(data, dict):
        issues.append("Manifest root must be a YAML mapping")
        return issues

    # Required fields
    for field in sorted(_AGENT_REQUIRED):
        if field not in data:
            issues.append(f"Missing required field: {field}")

    # system_prompt_ref — defaults to system_prompt.md when absent
    prompt_ref: str = data.get("system_prompt_ref", "system_prompt.md")
    if not (agent_dir / prompt_ref).exists():
        issues.append(f"System prompt not found: {prompt_ref}")

    # Optional side-car files
    hooks_ref: str | None = data.get("hooks_ref")
    if hooks_ref and not (agent_dir / hooks_ref).exists():
        issues.append(f"Hooks file not found: {hooks_ref}")

    grading_ref: str | None = data.get("grading_criteria_ref")
    if grading_ref and not (agent_dir / grading_ref).exists():
        issues.append(f"Grading criteria not found: {grading_ref}")

    # tools must be a list
    tools = data.get("tools", [])
    if not isinstance(tools, list):
        issues.append(f"'tools' must be a list, got {type(tools).__name__}")

    # execution_mode — accept both string and mapping forms
    em = data.get("execution_mode")
    if em is not None:
        if isinstance(em, dict):
            # New-style: {primary: plan_execute, fallback: react, ...}
            mode_name: str | None = em.get("primary") or em.get("name")
        elif isinstance(em, str):
            mode_name = em
        else:
            mode_name = None
            issues.append(f"'execution_mode' must be a string or mapping, got {type(em).__name__}")

        if mode_name and mode_name not in _VALID_MODES:
            issues.append(f"Unknown execution_mode: {mode_name}")

    return issues


def validate_workflow(workflow_path: str | Path) -> list[str]:
    """Validate a workflow YAML file.

    Checks required top-level fields, step structure, dependency references,
    gate on_fail values, and feedback loop references.

    Args:
        workflow_path: Path to a workflow ``.yaml`` file.

    Returns:
        A list of human-readable issue strings.  Empty list means the workflow is valid.

    Example::

        issues = validate_workflow("workflows/genesis_build.yaml")
        if issues:
            for msg in issues:
                print(f"  - {msg}")
    """
    issues: list[str] = []
    wf_path = Path(workflow_path)

    if not wf_path.exists():
        return [f"Workflow file not found: {wf_path}"]

    try:
        data: dict[str, Any] = yaml.safe_load(wf_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        issues.append(f"Invalid YAML: {exc}")
        return issues

    if not isinstance(data, dict):
        issues.append("Workflow root must be a YAML mapping")
        return issues

    # Required top-level fields
    for field in sorted(_WORKFLOW_REQUIRED):
        if field not in data:
            issues.append(f"Missing required field: {field}")

    steps: list[Any] = data.get("steps", [])
    if not isinstance(steps, list):
        issues.append("'steps' must be a list")
        return issues

    # Build a full set of step names for forward-reference checks
    all_step_names: set[str] = {
        s.get("name", f"step_{i}")
        for i, s in enumerate(steps)
        if isinstance(s, dict)
    }

    step_names_seen: set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            issues.append(f"Step {i} is not a mapping")
            continue

        for field in sorted(_STEP_REQUIRED):
            if field not in step:
                issues.append(f"Step {i}: missing required field '{field}'")

        name: str = step.get("name", f"step_{i}")

        # Duplicate detection
        if name in step_names_seen:
            issues.append(f"Duplicate step name: {name}")
        step_names_seen.add(name)

        # depends_on references
        depends_on: list[str] = step.get("depends_on", [])
        if not isinstance(depends_on, list):
            issues.append(f"Step '{name}': 'depends_on' must be a list")
        else:
            for dep in depends_on:
                if dep not in all_step_names:
                    issues.append(
                        f"Step '{name}': depends_on '{dep}' not found in workflow"
                    )

        # gate validation
        gate: Any = step.get("gate")
        if gate is not None:
            if not isinstance(gate, dict):
                issues.append(f"Step '{name}': 'gate' must be a mapping")
            else:
                on_fail: str | None = gate.get("on_fail")
                if on_fail and on_fail not in _VALID_ON_FAIL:
                    issues.append(f"Step '{name}': unknown gate on_fail: {on_fail}")

                gate_type: str | None = gate.get("type")
                if gate_type and gate_type not in _VALID_GATE_TYPES:
                    issues.append(f"Step '{name}': unknown gate type: {gate_type}")

    # feedback_loops
    for loop in data.get("feedback_loops", []):
        if not isinstance(loop, dict):
            continue
        from_step: str | None = loop.get("from_step")
        to_step: str | None = loop.get("to_step")
        if from_step and from_step not in all_step_names:
            issues.append(f"Feedback loop: from_step '{from_step}' not found")
        if to_step and to_step not in all_step_names:
            issues.append(f"Feedback loop: to_step '{to_step}' not found")

    return issues
