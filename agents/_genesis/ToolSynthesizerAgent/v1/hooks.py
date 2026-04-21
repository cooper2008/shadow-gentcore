"""Post-execute hook for ToolSynthesizerAgent.

The agent emits a `synthesized_tools: [{gap_name, decision, pack_id,
pack_yaml, ...}, ...]` array. This hook:

1. Writes each emitted `pack_yaml` into `{domain_path}/tools/auto/<slug>.yaml`.
2. Carries the deduplicated `credentials_needed` up into the step output
   so downstream Builder can union them with domain-agent credentials.
3. Populates gate-visible status: files_written, packs_written, decisions
   summary.

Phase A: emit-then-write only. Phase B (separate commit) adds the
static security scanner that gate-blocks risky packs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _resolve_output_dict(result: Any) -> dict[str, Any] | None:
    """Find the agent's structured output regardless of parser placement."""
    if not isinstance(result, dict):
        return None
    for key in ("output", "parsed_output"):
        val = result.get(key)
        if isinstance(val, dict):
            return val
    content = result.get("content")
    if isinstance(content, str) and content.strip().startswith("{"):
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _resolve_domain_path(task: Any) -> str:
    if isinstance(task, dict):
        for key in ("domain_path", "output_dir"):
            val = task.get(key)
            if val:
                return str(val)
            inner = task.get("input_payload")
            if isinstance(inner, dict) and inner.get(key):
                return str(inner[key])
    return "."


def _slug_from_pack_id(pack_id: str, fallback: str) -> str:
    """Derive a safe filename from `toolpack://auto/xxx` or `mcp/xxx`."""
    m = re.match(r"toolpack://[^/]+/(.+)", pack_id or "")
    slug = m.group(1) if m else fallback
    slug = re.sub(r"[^A-Za-z0-9_\-]", "_", slug).strip("_") or "pack"
    return slug


def post_execute(manifest: Any, task: Any, result: Any) -> Any:
    """Write synthesized pack YAMLs + summarize for the gate."""
    output = _resolve_output_dict(result)
    if not output or not isinstance(output.get("synthesized_tools"), list):
        # Agent emitted something off-schema (or nothing) — preserve as-is.
        return result

    domain_path = _resolve_domain_path(task)
    auto_dir = Path(domain_path).expanduser().resolve() / "tools" / "auto"
    tools = output["synthesized_tools"]

    written: list[str] = []
    failed: list[dict[str, str]] = []
    decisions_by_kind: dict[str, int] = {
        "reuse-existing-mcp": 0,
        "synthesize-new": 0,
        "unreachable": 0,
    }

    for idx, tool in enumerate(tools):
        if not isinstance(tool, dict):
            continue
        decision = tool.get("decision", "")
        if decision in decisions_by_kind:
            decisions_by_kind[decision] += 1

        pack_yaml = tool.get("pack_yaml")
        if not pack_yaml:
            # Unreachable entries don't produce a file — that's expected.
            continue

        pack_id = tool.get("pack_id") or f"toolpack://auto/gap_{idx}"
        fallback = tool.get("gap_name") or f"gap_{idx}"
        slug = _slug_from_pack_id(pack_id, fallback)
        target = auto_dir / f"{slug}.yaml"

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(pack_yaml, encoding="utf-8")
            written.append(str(target))
        except Exception as exc:
            failed.append({"pack_id": pack_id, "error": str(exc)[:200]})

    enriched = {
        "auto_dir": str(auto_dir),
        "packs_written": written,
        "packs_failed": failed,
        "decisions": decisions_by_kind,
        "synthesized_tools": tools,  # preserve for architect consumption
        "credentials_needed": output.get("credentials_needed", []),
        "classification": output.get("classification", {}),
        "build_plan": output.get("build_plan", {}),
        "security_scan": {
            "passed": True,    # Phase A default; Phase B's scanner will overwrite.
            "issues": [],
            "skipped": "Phase A — static scanner lands in Phase B",
        },
    }

    if isinstance(result, dict):
        result["output"] = enriched
        if failed and not written:
            result["status"] = "error"
            result["error"] = f"All {len(failed)} pack writes failed"
        elif failed:
            result["status"] = "partial"
        else:
            result["status"] = "success"
    return result
