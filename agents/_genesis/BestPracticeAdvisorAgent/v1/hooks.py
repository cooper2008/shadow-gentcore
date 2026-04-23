"""Post-execute hook for BestPracticeAdvisorAgent.

The advisor emits `files: [{path, content}]` (currently exactly one
entry for `context/best_practices.md`). This hook writes the file to
disk under the domain root — same emit-then-write pattern
AgentBuilderAgent uses — and mirrors the enriched output onto
`result.parsed_output` so the advise_gate can evaluate it.

Keeps the advisor's own `permissions.file_create: deny` intact: the
LLM never touches the filesystem; the framework-trusted hook does.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


def _resolve_files(result: Any) -> list[dict[str, Any]]:
    """Pull files[] out regardless of where the parser placed it."""
    if not isinstance(result, dict):
        return []
    for key in ("output", "parsed_output", "content"):
        val = result.get(key)
        if isinstance(val, dict) and isinstance(val.get("files"), list):
            return val["files"]
        if isinstance(val, str) and val.strip().startswith("{"):
            try:
                parsed = json.loads(val)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("files"), list):
                return parsed["files"]
    return []


def _resolve_output_dir(task: Any) -> str:
    if isinstance(task, dict):
        od = task.get("output_dir")
        if not od and isinstance(task.get("input_payload"), dict):
            od = task["input_payload"].get("output_dir")
        if not od:
            od = task.get("domain_path") or task.get("domain_root")
        if od:
            return str(od)
    return "."


def post_execute(manifest: Any, task: Any, result: Any) -> Any:
    """Write emitted files to disk and reflect on the result wrapper.

    When `files` is absent the advisor ran but produced nothing
    actionable (very small library or coverage complete). Leave
    result unchanged in that case so the downstream gate sees a
    completion without crashing on a missing field.
    """
    files = _resolve_files(result)
    if not files:
        return result

    output_dir = _resolve_output_dir(task)
    root = Path(output_dir).expanduser().resolve()

    written: list[str] = []
    failed: list[dict[str, str]] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get("path")
        content = entry.get("content", "")
        if not raw_path or not isinstance(content, str):
            failed.append({"path": str(raw_path), "error": "missing path or non-string content"})
            continue
        p = Path(raw_path)
        if not p.is_absolute():
            p = root / p
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            written.append(str(p))
        except Exception as exc:
            failed.append({"path": str(raw_path), "error": str(exc)[:200]})

    # Preserve whatever the LLM emitted (gap_summary, best_practices_md)
    # and append the hook's own write-result so the gate can see both.
    existing = {}
    if isinstance(result, dict):
        for key in ("parsed_output", "output"):
            val = result.get(key)
            if isinstance(val, dict):
                existing = val
                break

    enriched = dict(existing)
    enriched["overlay_written"] = len(written) > 0
    enriched["overlay_path"] = written[0] if written else ""
    enriched["files_failed"] = failed

    if isinstance(result, dict):
        result["output"] = enriched
        # See agent_runner.py:~382 — the wrapper lifts `parsed_output`
        # keys, so mirror there too (same fix AgentBuilderAgent needed).
        result["parsed_output"] = enriched
        if failed and not written:
            result["status"] = "error"
            result["error"] = f"All {len(failed)} overlay writes failed"
    return result
