"""Stage-grouped workflow printer (S4).

Groups workflow steps by the `stage:` tag on each step's agent manifest
and renders a grouped block view. Useful for CLI observability — humans
scanning a generated workflow see WHAT kind of work each step does
(analyze / generate / review / execute / ...) rather than an opaque list.

Audit §8 S4 — ~80 LOC, observability only. Works both with stage-tagged
manifests (post-G1) and untagged manifests (pre-G1) via an "untagged"
fallback bucket.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


# Stage ordering for consistent grouped output. Unknown stages append at
# the end in insertion order. Mirrors the Stage enum proposed in G1.
_STAGE_ORDER = (
    "analyze",
    "generate",
    "review",
    "execute",
    "respond",
    "summarize",
    "retrieve",
)


def _resolve_agent_dir(agent_id: str, project_root: Path) -> Path | None:
    """Best-effort lookup of an agent's directory from its id.

    Tries the two conventional layouts:
      - project_root/agents/<domain>/<AgentName>/<version>/
      - project_root/agents/<AgentName>/<version>/  (domain-local)

    Returns None when neither exists.
    """
    parts = agent_id.split("/")
    if len(parts) >= 3:
        candidate = project_root / "agents" / parts[0] / parts[1] / parts[2]
        if candidate.exists():
            return candidate
        candidate2 = project_root / "agents" / parts[1] / parts[2]
        if candidate2.exists():
            return candidate2
    return None


def _stage_for_agent(agent_id: str, project_root: Path, lookup: dict[str, str] | None = None) -> str:
    """Return the stage tag for an agent, or 'untagged' if unknown.

    Args:
        agent_id: agent reference as it appears in the workflow `agent:` field.
        project_root: repo root for manifest lookups.
        lookup: optional pre-populated `{agent_id: stage}` map that bypasses
            filesystem reads (useful for tests + cached CLI invocations).
    """
    if lookup is not None and agent_id in lookup:
        return (lookup[agent_id] or "untagged").lower()
    agent_dir = _resolve_agent_dir(agent_id, project_root)
    if agent_dir is None:
        return "untagged"
    manifest_path = agent_dir / "agent_manifest.yaml"
    if not manifest_path.exists():
        return "untagged"
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return "untagged"
    stage = manifest.get("stage")
    return str(stage).lower() if stage else "untagged"


def group_steps_by_stage(
    workflow: dict[str, Any],
    project_root: Path | None = None,
    lookup: dict[str, str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Group a workflow's steps by the `stage:` tag on their agent manifests.

    Returns an ordered dict: stages appear in _STAGE_ORDER first (only if
    they contain steps), then any unknown stages in first-seen order, with
    "untagged" last. Steps within a bucket preserve the original workflow
    order so `depends_on` reading still makes sense.
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent
    steps = workflow.get("steps") or []
    buckets: dict[str, list[dict[str, Any]]] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        agent_id = step.get("agent", "")
        stage = _stage_for_agent(agent_id, project_root, lookup)
        buckets.setdefault(stage, []).append(step)

    ordered: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for stage in _STAGE_ORDER:
        if stage in buckets:
            ordered[stage] = buckets[stage]
            seen.add(stage)
    for stage, bucket in buckets.items():
        if stage in seen or stage == "untagged":
            continue
        ordered[stage] = bucket
    if "untagged" in buckets:
        ordered["untagged"] = buckets["untagged"]
    return ordered


def format_grouped_workflow(
    workflow: dict[str, Any],
    project_root: Path | None = None,
    lookup: dict[str, str] | None = None,
) -> str:
    """Format a workflow as a human-readable stage-grouped block.

    Example output::

        Workflow: genesis_build
        ────────────────────────────
        [analyze]
          scan          _genesis/SourceScannerAgent/v1
          map           _genesis/KnowledgeMapperAgent/v1
        [generate]
          build         _genesis/AgentBuilderAgent/v1
        [review]
          validate      _genesis/QualityGateAgent/v1

    Returns a newline-joined string suitable for print() or click.echo().
    """
    grouped = group_steps_by_stage(workflow, project_root=project_root, lookup=lookup)
    lines: list[str] = []
    name = workflow.get("name", "<unnamed>")
    lines.append(f"Workflow: {name}")
    lines.append("─" * 28)
    if not grouped:
        lines.append("  (no steps)")
        return "\n".join(lines)
    for stage, bucket in grouped.items():
        lines.append(f"[{stage}]")
        for step in bucket:
            step_name = step.get("name") or step.get("id", "?")
            agent_id = step.get("agent", "?")
            lines.append(f"  {step_name:<14} {agent_id}")
    return "\n".join(lines)
