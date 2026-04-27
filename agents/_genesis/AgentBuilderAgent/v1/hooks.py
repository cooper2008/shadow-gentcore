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

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


_GENESIS_MANIFEST_NAME = ".gentcore/genesis-manifest.json"


def _sha256(text: str) -> str:
    """SHA-256 of UTF-8 encoded text. Used to detect post-genesis hand edits."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _genesis_manifest_path(out_root: Path) -> Path:
    return out_root / _GENESIS_MANIFEST_NAME


def _load_genesis_manifest(path: Path) -> dict[str, Any]:
    """Load the per-domain genesis hash manifest. Returns empty dict on first run / read error."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_genesis_manifest(path: Path, data: dict[str, Any]) -> None:
    """Persist the genesis hash manifest. Writes are best-effort — failure
    falls through silently so a transient I/O glitch never crashes the
    build. The manifest is advisory: a missing or corrupt manifest only
    means the next run will treat existing files as freshly generated.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def _force_overwrite_enabled(task: Any) -> bool:
    """Three opt-in channels for force-overwriting hand-edited generated files:

    1. Env var ``GENTCORE_FORCE_OVERWRITE=1`` — handy for one-shot CLI runs.
    2. ``task.force_overwrite=True`` — top-level flag (workflow steps can pass it).
    3. ``task.input_payload.force_overwrite=True`` — deeper variant.

    Default is False — generated files that have been hand-edited since the
    last genesis are SKIPPED, not overwritten. This is the regen-safety
    contract: re-running ``./ai genesis build`` on a customized domain no
    longer silently destroys the user's edits.
    """
    if str(os.environ.get("GENTCORE_FORCE_OVERWRITE", "")).lower() in ("1", "true", "yes", "on"):
        return True
    if isinstance(task, dict):
        if task.get("force_overwrite") is True:
            return True
        ip = task.get("input_payload")
        if isinstance(ip, dict) and ip.get("force_overwrite") is True:
            return True
    return False


_EXECUTION_MODE_ALIASES: dict[str, str] = {
    # Architect/free-form categories → valid framework strategies.
    "reasoning": "chain_of_thought",
    "fast-codegen": "direct",
    "fast_codegen": "direct",
    "codegen": "direct",
    "single-shot": "direct",
    "single_shot": "direct",
    "single_turn": "direct",
    "research": "chain_of_thought",
    "analysis": "chain_of_thought",
    "planning": "plan_execute",
    "plan": "plan_execute",
    # Already-canonical values map to themselves, added defensively so the
    # remap step doesn't accidentally nuke a correct value.
    "react": "react",
    "chain_of_thought": "chain_of_thought",
    "plan_execute": "plan_execute",
    "self_ask": "self_ask",
    "tree_of_thought": "tree_of_thought",
    "direct": "direct",
}

_VALID_PRELOAD_SOURCES: frozenset[str] = frozenset({
    # Mirror the registered names in harness/core/manifest_loader.py::_build_preload_item.
    # Keep in sync — adding a new source there REQUIRES adding it here too,
    # otherwise the normalizer will silently strip it from generated manifests.
    "tool_pack_catalog",
    "shadow_gentcore_builtin_tools",
    "domain_context_docs",
    "shared_stage_catalog",
    "capabilities_config",
    "known_mcp_servers",
    "tool_security_policy",
    "project_file_tree",
    "domain_evolution_signals",
    "best_practices_overlay",
    "best_practice_library",
})


_ON_FAIL_ALIASES: dict[str, str] = {
    "continue": "degrade",
    "skip": "degrade",
    "fail": "abort",
    "stop": "abort",
    "escalate": "escalate_human",
    "rollback_to": "rollback",
    # canonical
    "retry": "retry",
    "retry_fresh": "retry_fresh",
    "rollback": "rollback",
    "abort": "abort",
    "escalate_human": "escalate_human",
    "degrade": "degrade",
    "fallback": "fallback",
}


def _normalize_enums(path: str, content: str) -> str:
    """Rewrite common GLM/MiniMax enum mis-emissions before we write to disk.

    Genesis LLMs frequently confuse the architect's free-form `category`
    label (e.g. `reasoning`, `fast-codegen`) with the framework's
    `execution_mode.primary` enum, and similarly emit `on_fail: continue`
    / `on_fail: fail` in workflows where the schema expects
    `degrade`/`abort`. `schema_validator` would later reject those as
    `Unknown execution_mode: …` / `unknown gate on_fail: …`, breaking
    downstream runs. Normalising at write-time is a defensive net: the
    Builder prompt carries the same guidance but real-model output
    still drifts.

    Applies ONLY to agent_manifest.yaml and workflow YAMLs — detected by
    filename — so ordinary docs/prompts aren't touched.
    """
    if not content or not isinstance(content, str):
        return content
    p_lower = path.lower()
    is_agent = p_lower.endswith("agent_manifest.yaml")
    is_workflow = (
        (p_lower.startswith("workflows/") or "/workflows/" in p_lower)
        and p_lower.endswith((".yaml", ".yml"))
    )
    if not (is_agent or is_workflow):
        return content

    import re
    out = content

    if is_agent:
        # `primary: reasoning` → `primary: chain_of_thought`
        def _mode_sub(m: "re.Match[str]") -> str:
            indent, value = m.group(1), m.group(2).strip().strip("'\"")
            canonical = _EXECUTION_MODE_ALIASES.get(value.lower(), value)
            return f"{indent}primary: {canonical}"
        out = re.sub(r"(^[ \t]*)primary:[ \t]+([A-Za-z0-9_\-]+)",
                     _mode_sub, out, flags=re.MULTILINE)

    if is_workflow:
        def _on_fail_sub(m: "re.Match[str]") -> str:
            indent, value = m.group(1), m.group(2).strip().strip("'\"")
            canonical = _ON_FAIL_ALIASES.get(value.lower(), value)
            return f"{indent}on_fail: {canonical}"
        out = re.sub(r"(^[ \t]*)on_fail:[ \t]+([A-Za-z0-9_\-]+)",
                     _on_fail_sub, out, flags=re.MULTILINE)

    return out


def _normalize_agent_manifest_schema(path: str, content: str) -> str:
    """Fix two recurring schema drifts in LLM-generated agent manifests.

    Drift 1 — ``constraints:`` emitted as a list of free-form strings.
       AgentManifest expects a ``ConstraintsConfig`` dict
       (``allowed_paths``, ``blocked_commands``, ``max_file_changes``,
       ``max_lines_per_file``, ``require_tests``). When the LLM emits a
       list, we move the strings to ``metadata.constraint_notes`` (so
       they're preserved for human readers) and replace ``constraints``
       with an empty ``{}`` so the schema validates.

    Drift 2 — ``context.preload`` includes invented source names.
       Only a fixed set of preload sources is registered in
       ``manifest_loader._build_preload_item``. Names like
       ``fastapi_patterns`` or ``standards`` silently produce nothing at
       runtime — the agent loses the context it expected to see. We
       drop unknown entries (keeping the registered ones) and stash the
       removed names in ``metadata.dropped_preload_sources`` so the
       human reader can see what got pruned.

    Applies ONLY to agent_manifest.yaml (filename match) so workflow
    YAMLs and other docs are untouched. Round-trips through PyYAML —
    on parse failure the original content passes through unchanged so
    a malformed manifest still gets written for human inspection.
    """
    if not content or not isinstance(content, str):
        return content
    if not path.lower().endswith("agent_manifest.yaml"):
        return content

    try:
        import yaml as _yaml
        data = _yaml.safe_load(content)
    except Exception:
        return content
    if not isinstance(data, dict):
        return content

    changed = False

    # --- Drift 0: missing permissions block ---
    # AgentManifest's `permissions: PermissionPolicy` field has a default_factory,
    # so pydantic accepts a manifest without it. But the smoke runner (and
    # human reviewers) treat the missing block as an incomplete manifest. Add
    # an explicit default block — chosen by category so code-writing agents
    # don't ship with restrictive `file_edit: ask` defaults that would block
    # their first file write at runtime.
    if "permissions" not in data:
        category = str(data.get("category") or "").lower()
        # Code-writing categories need file_edit + shell_command allow
        if any(t in category for t in ("codegen", "code", "fast", "direct", "writer", "migration", "test")):
            data["permissions"] = {
                "file_edit": "allow",
                "shell_command": "allow",
                "external_api": "deny",
                "browser": "deny",
            }
        # Review/analysis/reasoning agents stay safe-by-default
        else:
            data["permissions"] = {
                "file_edit": "deny",
                "shell_command": "ask",
                "external_api": "deny",
                "browser": "deny",
            }
        changed = True

    # --- Drift 0aa: missing provider block — auto-resolve from tier registry ---
    # When the LLM omits `provider:` (Gemini Flash often does for non-codegen
    # agents), pick a tier-appropriate model from config/model_tiers.yaml.
    # Code-writing agents will get Claude/GLM (codegen-strong); triage agents
    # get Gemini Flash / M2.7 (classification-light); etc. Writes only when
    # creds are available — otherwise leaves the manifest's domain-default
    # provider intact (caller still works, just at the domain-wide model).
    # Marked with `_resolved_tier` so humans can spot framework picks.
    # Disable globally with GENTCORE_AUTO_PROVIDER=0.
    import os as _os
    if (
        "provider" not in data
        and _os.environ.get("GENTCORE_AUTO_PROVIDER", "1") != "0"
    ):
        try:
            from harness.core.provider_resolver import (
                load_tiers, resolve_provider_for_agent,
            )
            tiers_doc = load_tiers()
            agent_id = data.get("id")
            spec = resolve_provider_for_agent(
                agent_id=str(agent_id) if agent_id else None,
                category=str(data.get("category") or ""),
                tiers_doc=tiers_doc,
            )
            if spec is not None:
                data["provider"] = spec
                changed = True
        except Exception:
            # Resolver unavailable (e.g. registry malformed) — silently skip.
            # Domain-default provider still applies at runtime.
            pass

    # --- Drift 0a: missing system_prompt_ref ---
    # AgentManifest declares this as REQUIRED. MiniMax M2.7 commonly omits
    # it in roster output; Pydantic then fails the manifest at load time.
    # Builder always writes a system_prompt.md alongside the manifest, so
    # default the ref to that filename — it's almost always correct.
    if "system_prompt_ref" not in data:
        data["system_prompt_ref"] = "system_prompt.md"
        changed = True

    # --- Drift 0b: missing input_schema / output_schema ---
    # AgentManifest declares both as optional (default {}) so pydantic accepts
    # the manifest, but downstream the smoke runner + Builder gates treat the
    # absence as incomplete (Gemini-Flash truncation often drops them when its
    # max_tokens hits mid-roster). Drop in a minimal pass-through so the agent
    # is still wireable; humans can tighten later. Schema-strict callers can
    # always set GENTCORE_STRICT_MANIFESTS=1 — these defaults are still
    # schema-valid.
    if "input_schema" not in data:
        data["input_schema"] = {
            "type": "object",
            "description": "Auto-defaulted by Builder normalizer (LLM omitted input_schema).",
            "properties": {},
        }
        changed = True
    # Detect a previous run's auto-defaulted output_schema so this pass
    # can upgrade it (e.g. from generic empty {} → code-aware files array).
    _prev_default_marker = "Auto-defaulted by Builder normalizer"
    _existing_os = data.get("output_schema")
    _is_old_auto_default = (
        isinstance(_existing_os, dict)
        and _prev_default_marker in str(_existing_os.get("description") or "")
        and not _existing_os.get("required")
    )
    if "output_schema" not in data or _is_old_auto_default:
        # Category-aware default: code-writing categories must declare a
        # `files: [{path, content}]` array so AgentRunner._persist_output_files
        # picks them up. Pre-fix the empty {} default left CodeWriter unable
        # to emit files — workflow ran but produced nothing on disk. This is
        # the bridge between "manifest is schema-valid" and "agent actually
        # writes files when run".
        category = str(data.get("category") or "").lower()
        if any(t in category for t in ("codegen", "code", "fast", "direct", "writer", "migration")):
            data["output_schema"] = {
                "type": "object",
                "description": (
                    "Auto-defaulted by Builder normalizer. Code-writing agents "
                    "MUST emit a `files` array; the framework persists each entry "
                    "under task.workspace_root."
                ),
                "required": ["files"],
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["path", "content"],
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                        },
                    },
                },
            }
        else:
            data["output_schema"] = {
                "type": "object",
                "description": "Auto-defaulted by Builder normalizer (LLM omitted output_schema).",
                "properties": {},
            }
        changed = True

    # --- Drift 1: constraints as list ---
    constraints = data.get("constraints")
    if isinstance(constraints, list):
        notes = [str(c) for c in constraints if c is not None]
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        if not isinstance(meta, dict):
            meta = {}
        meta.setdefault("constraint_notes", []).extend(notes)
        data["metadata"] = meta
        data["constraints"] = {}
        changed = True
    elif "constraints" not in data:
        # --- Drift 1b: missing constraints field altogether ---
        # Smoke runner flags this as incomplete. AgentManifest accepts the
        # absence (default {}) but we make it explicit for the runner +
        # human reviewers. Empty dict = "no agent-specific constraints".
        data["constraints"] = {}
        changed = True

    # --- Drift 2: invalid context.preload entries ---
    ctx = data.get("context")
    if isinstance(ctx, dict):
        preload = ctx.get("preload")
        if isinstance(preload, list):
            kept = [p for p in preload if isinstance(p, str) and p in _VALID_PRELOAD_SOURCES]
            dropped = [p for p in preload if isinstance(p, str) and p not in _VALID_PRELOAD_SOURCES]
            if dropped:
                ctx["preload"] = kept
                meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
                if not isinstance(meta, dict):
                    meta = {}
                meta.setdefault("dropped_preload_sources", []).extend(dropped)
                data["metadata"] = meta
                changed = True

    if not changed:
        return content
    try:
        return _yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    except Exception:
        return content


def _normalize_workflow_schema(path: str, content: str) -> str:
    """Fill missing ``gate:`` blocks on workflow steps.

    Drift — Gemini-Flash and other tier-2 models often emit workflow YAML
    where some steps have ``gate:`` and others don't. Smoke runner flags
    each gateless step as a structural failure. Add a permissive default
    so at minimum the workflow is structurally valid; humans can tighten.

    Default gate: ``{name: <step>_gate, condition: status == success,
    on_fail: retry, max_retries: 1}``. Mirrors the most common pattern
    Builder template emits already.

    Applies ONLY to ``workflows/*.yaml``. Round-trips through PyYAML — on
    parse failure or non-workflow shape, original content passes through.
    """
    if not content or not isinstance(content, str):
        return content
    p_lower = path.lower()
    is_workflow = (
        (p_lower.startswith("workflows/") or "/workflows/" in p_lower)
        and p_lower.endswith((".yaml", ".yml"))
    )
    if not is_workflow:
        return content
    try:
        import yaml as _yaml
        data = _yaml.safe_load(content)
    except Exception:
        return content
    if not isinstance(data, dict):
        return content
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        return content

    changed = False
    for step in steps:
        if not isinstance(step, dict):
            continue
        if "gate" not in step or step.get("gate") is None:
            step_name = str(step.get("name") or step.get("agent") or "step").rsplit("/", 1)[-1]
            step["gate"] = {
                "name": f"{step_name}_gate",
                "condition": "status == success",
                "on_fail": "retry",
                "max_retries": 1,
            }
            changed = True

    if not changed:
        return content
    try:
        return _yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    except Exception:
        return content


def _normalize_domain_manifest_schema(path: str, content: str) -> str:
    """Fix the recurring DomainManifest drift in genesis output.

    Drift — ``workflows:`` emitted as a list of file paths.
       DomainManifest.workflows expects an Optional[WorkflowsConfig] dict
       (with ``processes: [...]`` inside). When the LLM emits a list of
       paths, we convert the path-stripped basenames into
       ``workflows.processes``. The original list is preserved at
       ``metadata.declared_workflow_files`` so human readers can see
       what genesis intended.

    Applies ONLY to ``domain.yaml``. Round-trips through PyYAML — on
    parse failure the original content passes through unchanged.
    """
    if not content or not isinstance(content, str):
        return content
    if not path.lower().endswith("domain.yaml"):
        return content
    try:
        import yaml as _yaml
        data = _yaml.safe_load(content)
    except Exception:
        return content
    if not isinstance(data, dict):
        return content

    workflows = data.get("workflows")
    if not isinstance(workflows, list):
        return content

    process_names: list[str] = []
    for entry in workflows:
        if not isinstance(entry, str):
            continue
        # strip "workflows/" prefix and ".yaml"/".yml" suffix
        base = entry
        if "/" in base:
            base = base.rsplit("/", 1)[1]
        for suffix in (".yaml", ".yml"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        process_names.append(base)

    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    meta.setdefault("declared_workflow_files", []).extend(workflows)
    data["metadata"] = meta
    data["workflows"] = {"processes": process_names}

    try:
        return _yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    except Exception:
        return content


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

    # Regen-safety: load the per-domain genesis hash manifest. We use it to
    # detect files the user has hand-edited since the last `genesis build`
    # and skip them by default. Set GENTCORE_FORCE_OVERWRITE=1 (or pass
    # force_overwrite=True on the task) to override.
    genesis_manifest_path = _genesis_manifest_path(out_root)
    genesis_manifest = _load_genesis_manifest(genesis_manifest_path)
    force_overwrite = _force_overwrite_enabled(task)
    now = time.time()

    written: list[str] = []
    failed: list[dict[str, str]] = []
    skipped_user_modified: list[dict[str, str]] = []
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
            content = _normalize_enums(raw_path, content)
            content = _normalize_agent_manifest_schema(raw_path, content)
            content = _normalize_domain_manifest_schema(raw_path, content)
            content = _normalize_workflow_schema(raw_path, content)
            rel = str(p.relative_to(out_root)) if str(p).startswith(str(out_root)) else str(p)

            # Regen-safety check: if file already exists AND its current
            # hash differs from the last-genesis hash we recorded, the user
            # has edited it. Skip unless force_overwrite is on.
            if p.exists() and not force_overwrite:
                prior_record = genesis_manifest.get(rel)
                if isinstance(prior_record, dict):
                    prior_hash = prior_record.get("hash")
                    if prior_hash:
                        try:
                            current_hash = _sha256(p.read_text(encoding="utf-8"))
                        except (OSError, UnicodeDecodeError):
                            current_hash = None
                        if current_hash and current_hash != prior_hash:
                            skipped_user_modified.append({
                                "path": rel,
                                "reason": "file modified since last genesis (set GENTCORE_FORCE_OVERWRITE=1 to override)",
                            })
                            continue

            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            written.append(str(p))
            # Record the hash of what we just wrote so the next genesis run
            # can tell whether the user has hand-edited the file.
            genesis_manifest[rel] = {"hash": _sha256(content), "generated_at": now}

            # Classify: agents/<Name>/v1/... ; workflows/<name>.yaml
            parts = rel.split(os.sep)
            if len(parts) >= 2 and parts[0] == "agents":
                agents_created.add(parts[1])
            elif len(parts) >= 2 and parts[0] == "workflows" and rel.endswith(".yaml"):
                workflows_created.append(Path(rel).stem)
        except Exception as exc:
            failed.append({"path": str(raw_path), "error": str(exc)[:200]})

    # Persist the updated genesis manifest. Best-effort — _save's OSError
    # swallow ensures a transient I/O glitch never crashes the build.
    if written:
        _save_genesis_manifest(genesis_manifest_path, genesis_manifest)

    files_planned = len(files)
    files_written = len(written)
    files_skipped = len(skipped_user_modified)
    # Treat regen-skips as completed for gate purposes — the user EXPLICITLY
    # owns those files now, so the build is "done" with respect to them.
    # Genuine failures (failed list) still count against completion.
    files_completed = files_written + files_skipped
    completion_pct = (files_completed / files_planned * 100) if files_planned else 0

    enriched_output: dict[str, Any] = {
        "domain_dir": str(out_root),
        "files_created": written,
        "files_failed": failed,
        "files_skipped_user_modified": skipped_user_modified,
        "agents_created": sorted(agents_created),
        "workflows_created": workflows_created,
        "build_quality": {
            "files_planned": files_planned,
            "files_written": files_written,
            "files_skipped_user_modified": files_skipped,
            "completion_pct": completion_pct,
        },
    }

    # Credential auto-propagation — derive each agent's required_credentials
    # from its declared tools by consulting the CredentialRegistry (which
    # reads tool pack YAMLs' `credentials:` blocks). Rewrite agent manifests
    # in place with the derived field, and write REQUIRED_CREDENTIALS.md at
    # the domain root for the team-lead checklist.
    cred_summary = _propagate_credentials(out_root, written)
    if cred_summary:
        enriched_output["credentials"] = cred_summary

    if isinstance(result, dict):
        result["output"] = enriched_output
        # The AgentRunner's promotion path reads `parsed_output` (set by
        # OutputParser from the raw LLM emission) and lifts its keys to
        # the top level where gates see them. Without also updating
        # `parsed_output`, `build_quality` / `files_created` never
        # surface and `output.build_quality.files_written >= 3` fails
        # despite the files actually being on disk. See agent_runner.py
        # line ~382 for the read site.
        result["parsed_output"] = enriched_output
        # status should reflect actual write success, not just LLM completion
        if failed and not written:
            result["status"] = "error"
            result["error"] = f"All {len(failed)} file writes failed"
        elif failed:
            result["status"] = "partial"
        else:
            result["status"] = "success"
    return result


def _propagate_credentials(domain_root: Path, written_paths: list[str]) -> dict[str, Any]:
    """Derive required_credentials per agent and emit REQUIRED_CREDENTIALS.md.

    Returns summary dict: {agents: {<name>: [cred_names]}, total_unique: int}
    or empty dict if the registry cannot be loaded.
    """
    try:
        import yaml
        # Locate agent-tools packs dir (same pattern as manifest_loader preload)
        import agent_tools
        from harness.core.credential_registry import CredentialRegistry, CredentialBackend
    except Exception:
        # agent_tools not importable (dev env quirk) — skip propagation cleanly
        return {}

    packs_root = Path(agent_tools.__file__).parent / "packs"
    if not packs_root.exists():
        return {}

    # Boot a registry with a no-op backend (we only need requirement lookup,
    # not value resolution). Register every pack YAML so tool→credentials
    # index is complete.
    class _NullBackend(CredentialBackend):  # type: ignore[misc]
        def resolve(self, name: str) -> str | None:
            return None

    registry = CredentialRegistry(backend=_NullBackend())
    for pack_yaml in packs_root.rglob("*.yaml"):
        try:
            pack = yaml.safe_load(pack_yaml.read_text(encoding="utf-8")) or {}
            registry.register_tool_pack(pack)
        except Exception:
            continue

    # Find every agent_manifest.yaml we just wrote
    per_agent: dict[str, list[dict[str, Any]]] = {}
    all_creds: dict[str, dict[str, Any]] = {}
    for p_str in written_paths:
        p = Path(p_str)
        if p.name != "agent_manifest.yaml":
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            continue

        agent_name = p.parent.parent.name  # agents/<Name>/v1/agent_manifest.yaml
        reqs = registry.required_for_agent(data)
        if not reqs:
            continue
        entries = [
            {
                "name": r.name,
                "purpose": r.purpose,
                "required": r.required,
                "auth_kind": getattr(r, "auth_kind", "api_key"),
                "oauth_group": getattr(r, "oauth_group", ""),
                "oauth_scopes": list(getattr(r, "oauth_scopes", ()) or ()),
            }
            for r in reqs
        ]
        per_agent[agent_name] = entries
        for r in reqs:
            all_creds.setdefault(r.name, {
                "purpose": r.purpose,
                "required": r.required,
                "auth_kind": getattr(r, "auth_kind", "api_key"),
                "oauth_group": getattr(r, "oauth_group", ""),
                "oauth_scopes": list(getattr(r, "oauth_scopes", ()) or ()),
                "used_by": [],
            })
            all_creds[r.name]["used_by"].append(agent_name)

        # Re-emit the manifest with required_credentials appended (idempotent —
        # skip if the field is already present to avoid file-churn on retries).
        if "required_credentials" not in data:
            data["required_credentials"] = [r.name for r in reqs]
            try:
                p.write_text(
                    yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
                    encoding="utf-8",
                )
            except Exception:
                pass

    # Write REQUIRED_CREDENTIALS.md at domain root, grouped by auth_kind so
    # OAuth setups (client_id + secret + refresh_token + scopes) read as one
    # coherent block instead of three scattered env-var rows.
    if all_creds:
        lines = _render_required_credentials_md(all_creds, per_agent)
        try:
            (domain_root / "REQUIRED_CREDENTIALS.md").write_text(
                "\n".join(lines), encoding="utf-8"
            )
        except Exception:
            pass

    return {
        "per_agent": per_agent,
        "unique_count": len(all_creds),
        "names": sorted(all_creds.keys()),
    }


def _render_required_credentials_md(
    all_creds: dict[str, dict[str, Any]],
    per_agent: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """Build REQUIRED_CREDENTIALS.md line-by-line, grouped by auth_kind.

    Sections (only present if the corresponding creds exist):
      * API-key + static creds (table)
      * OAuth 2.0 app setups (one block per oauth_group, with scopes list
        + setup hint)
      * Other auth kinds (basic, mtls, aws_iam) grouped under "Advanced"
      * Per-agent breakdown
    """
    api_key_creds: dict[str, dict[str, Any]] = {}
    oauth_groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    advanced: dict[str, dict[str, Any]] = {}

    for name, info in all_creds.items():
        kind = info.get("auth_kind", "api_key")
        if kind == "oauth2":
            group_key = info.get("oauth_group") or name
            oauth_groups.setdefault(group_key, []).append((name, info))
        elif kind in ("basic", "mtls", "aws_iam"):
            advanced[name] = info
        else:
            api_key_creds[name] = info

    lines: list[str] = [
        "# Required Credentials",
        "",
        "_Auto-generated by `AgentBuilderAgent` from each agent's declared tools._",
        "",
        "Set these before running any agent — via environment variables, "
        "`config/credentials.yaml`, or a configured secrets backend "
        "(AWS Secrets Manager / Vault). See `docs/CREDENTIALS_GUIDE.md`.",
        "",
    ]

    if api_key_creds:
        lines.append("## API keys and static credentials")
        lines.append("")
        lines.append("Set each via `export NAME=value` or add to `config/credentials.yaml`.")
        lines.append("")
        lines.append("| Credential | Purpose | Required | Used by |")
        lines.append("| --- | --- | --- | --- |")
        for name in sorted(api_key_creds):
            info = api_key_creds[name]
            purpose = (info["purpose"] or "").replace("\n", " ").replace("|", "\\|")
            required_icon = "✓" if info["required"] else "optional"
            agents = ", ".join(sorted(set(info["used_by"])))
            lines.append(f"| `{name}` | {purpose} | {required_icon} | {agents} |")
        lines.append("")

    if oauth_groups:
        lines.append("## OAuth 2.0 app setups")
        lines.append("")
        lines.append(
            "Each OAuth integration requires a one-time setup: register an "
            "OAuth app with the vendor, grant the listed scopes, and run "
            "the setup command below to obtain a refresh_token. Store the "
            "resulting env vars in your secrets backend — do NOT commit them."
        )
        lines.append("")
        for group_key in sorted(oauth_groups):
            entries = oauth_groups[group_key]
            scopes: set[str] = set()
            agents: set[str] = set()
            for _name, info in entries:
                for s in info.get("oauth_scopes") or []:
                    scopes.add(s)
                for a in info.get("used_by") or []:
                    agents.add(a)
            lines.append(f"### `{group_key}`")
            lines.append("")
            lines.append("**Credentials to obtain:**")
            lines.append("")
            for name, info in sorted(entries):
                purpose = (info["purpose"] or "").replace("\n", " ")
                lines.append(f"- `{name}` — {purpose}")
            if scopes:
                lines.append("")
                lines.append("**OAuth scopes requested:**")
                lines.append("")
                for s in sorted(scopes):
                    lines.append(f"- `{s}`")
            if agents:
                lines.append("")
                lines.append(f"**Used by:** {', '.join(sorted(agents))}")
            lines.append("")
            lines.append(
                f"**Setup:** run `./ai credentials oauth-setup {group_key}` once "
                "to complete the authorization flow and print the `export` "
                "commands for your shell or secrets backend."
            )
            lines.append("")

    if advanced:
        lines.append("## Advanced auth (basic / mutual TLS / AWS IAM)")
        lines.append("")
        lines.append("| Credential | Kind | Purpose | Required | Used by |")
        lines.append("| --- | --- | --- | --- | --- |")
        for name in sorted(advanced):
            info = advanced[name]
            kind = info.get("auth_kind", "api_key")
            purpose = (info["purpose"] or "").replace("\n", " ").replace("|", "\\|")
            required_icon = "✓" if info["required"] else "optional"
            agents = ", ".join(sorted(set(info["used_by"])))
            lines.append(f"| `{name}` | `{kind}` | {purpose} | {required_icon} | {agents} |")
        lines.append("")

    # Per-agent breakdown (always)
    lines.append("## Per-agent breakdown")
    lines.append("")
    for agent_name in sorted(per_agent):
        lines.append(f"### `{agent_name}`")
        lines.append("")
        for e in per_agent[agent_name]:
            marker = "required" if e["required"] else "optional"
            kind = e.get("auth_kind", "api_key")
            kind_label = f" [{kind}]" if kind != "api_key" else ""
            lines.append(f"- `{e['name']}`{kind_label} ({marker}) — {e['purpose']}")
        lines.append("")

    return lines
