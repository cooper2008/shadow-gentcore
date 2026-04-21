"""Post-execute hook for AgentBuilderAgent.

Builder runs as a single-turn agent emitting a `files: [{path, content}, ...]`
array in its structured output. This hook writes those files to disk under
`{task.output_dir}` and populates the downstream schema fields that the
build_gate evaluates (`files_created`, `files_failed`, `build_quality`,
`agents_created`, `workflows_created`, `domain_dir`).

Eliminates multi-turn file_write tool_use that hangs on MiniMax/GLM
Anthropic-compat endpoints and cuts Builder wall-clock from 20+ min
(often fruitlessly) to a single-turn completion plus fast disk I/O.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _resolve_files_from_result(result: Any) -> list[dict[str, Any]]:
    """Find the `files` array regardless of where the output parser placed it."""
    # Preferred: result["output"] is a dict with "files" key
    if isinstance(result, dict):
        for key in ("output", "parsed_output", "content"):
            val = result.get(key)
            if isinstance(val, dict) and isinstance(val.get("files"), list):
                return val["files"]
            # Sometimes `content` is a JSON string
            if isinstance(val, str) and val.strip().startswith("{"):
                try:
                    parsed = json.loads(val)
                except (ValueError, TypeError):
                    continue
                if isinstance(parsed, dict) and isinstance(parsed.get("files"), list):
                    return parsed["files"]
    return []


def _resolve_output_dir(task: Any) -> str:
    """Pull output_dir from task envelope; accept either top-level or input_payload."""
    if isinstance(task, dict):
        od = task.get("output_dir")
        if not od and isinstance(task.get("input_payload"), dict):
            od = task["input_payload"].get("output_dir")
        if not od:
            od = task.get("domain_path")
        if od:
            return str(od)
    return "."


def post_execute(manifest: Any, task: Any, result: Any) -> Any:
    """Write emitted files to disk and populate build_quality stats.

    Backward-compatibility: if the agent emitted the old schema
    (files_created / build_quality populated directly, no `files` array),
    skip the write and preserve the output as-is. The test provider +
    legacy callers use this path; real LLM calls emit the new `files`
    array and go through the write path.
    """
    files = _resolve_files_from_result(result)
    if not files:
        # Nothing to write — preserve whatever the agent emitted.
        return result
    output_dir = _resolve_output_dir(task)
    out_root = Path(output_dir).expanduser().resolve()

    written: list[str] = []
    failed: list[dict[str, str]] = []
    agents_created: set[str] = set()
    workflows_created: list[str] = []

    for entry in files:
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get("path")
        content = entry.get("content", "")
        if not raw_path or not isinstance(content, str):
            failed.append({"path": str(raw_path), "error": "missing path or non-string content"})
            continue
        # Normalize — treat as relative to output_dir unless absolute
        p = Path(raw_path)
        if not p.is_absolute():
            p = out_root / p
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            written.append(str(p))
            rel = str(p.relative_to(out_root)) if str(p).startswith(str(out_root)) else str(p)
            # Classify: agents/<Name>/v1/... ; workflows/<name>.yaml
            parts = rel.split(os.sep)
            if len(parts) >= 2 and parts[0] == "agents":
                agents_created.add(parts[1])
            elif len(parts) >= 2 and parts[0] == "workflows" and rel.endswith(".yaml"):
                workflows_created.append(Path(rel).stem)
        except Exception as exc:
            failed.append({"path": str(raw_path), "error": str(exc)[:200]})

    files_planned = len(files)
    files_written = len(written)
    completion_pct = (files_written / files_planned * 100) if files_planned else 0

    # Build the schema-compliant output the gate expects. We preserve the
    # original LLM output under `_raw` in case callers need it.
    enriched_output: dict[str, Any] = {
        "domain_dir": str(out_root),
        "files_created": written,
        "files_failed": failed,
        "agents_created": sorted(agents_created),
        "workflows_created": workflows_created,
        "build_quality": {
            "files_planned": files_planned,
            "files_written": files_written,
            "completion_pct": completion_pct,
        },
    }

    if isinstance(result, dict):
        result["output"] = enriched_output
        # status should reflect actual write success, not just LLM completion
        if failed and not written:
            result["status"] = "error"
            result["error"] = f"All {len(failed)} file writes failed"
        elif failed:
            result["status"] = "partial"
        else:
            result["status"] = "success"
    return result
