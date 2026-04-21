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
        import sys
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
