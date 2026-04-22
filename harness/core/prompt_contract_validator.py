"""Prompt ↔ manifest contract validator.

The claim: every agent's system_prompt.md promises a set of behaviors
(tools it uses, output fields it emits, pre-loaded context it reads,
execution mode it runs in). Every agent_manifest.yaml declares a
structural contract for the same behaviors. When they disagree, the
agent runs unpredictably — the LLM may try a tool that isn't registered,
emit a field the schema doesn't expect, or expect pre-loaded context
that was never wired in.

This module walks a domain's agent directory, parses each
(manifest.yaml, prompt.md) pair, and raises findings when they drift.

Use cases:
  * Run against every genesis-generated domain to catch Builder mistakes
    before deployment.
  * Run in CI on a hand-authored domain to catch human edits that drifted.
  * Invoked via `./ai validate contracts --domain <path>`.

This is static analysis — zero LLM calls, zero runtime cost.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ── Types ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContractFinding:
    rule_id: str
    severity: str  # "error" | "warn" | "info"
    agent_id: str
    location: str        # e.g. "manifest.tools" or "prompt:L42"
    message: str
    evidence: str = ""   # excerpt from the prompt or manifest that triggered

    def format_line(self) -> str:
        icon = {"error": "✗", "warn": "⚠", "info": "ℹ"}.get(self.severity, "•")
        ev = f" | evidence: {self.evidence[:80]}..." if self.evidence else ""
        return f"  {icon} [{self.severity.upper()}] {self.rule_id} @ {self.agent_id} — {self.message} ({self.location}){ev}"


@dataclass
class ContractReport:
    findings: list[ContractFinding] = field(default_factory=list)
    agents_checked: int = 0

    @property
    def errors(self) -> list[ContractFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warns(self) -> list[ContractFinding]:
        return [f for f in self.findings if f.severity == "warn"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def format_cli(self) -> str:
        if not self.findings:
            return f"✓ {self.agents_checked} agent(s) — all contracts aligned."
        lines = [
            f"Contract check: {len(self.errors)} error / {len(self.warns)} warn "
            f"across {self.agents_checked} agent(s)."
        ]
        for f in self.findings:
            lines.append(f.format_line())
        return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────


def validate_agent_contract(
    manifest_path: Path,
    prompt_path: Path | None = None,
) -> list[ContractFinding]:
    """Check one agent's (manifest, prompt) pair for internal consistency."""
    findings: list[ContractFinding] = []

    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return [ContractFinding(
            rule_id="manifest-parse-error", severity="error",
            agent_id=str(manifest_path), location="file",
            message=f"Cannot parse manifest: {str(exc)[:120]}",
        )]

    agent_id = str(manifest.get("id") or manifest_path.parent.parent.name)

    if prompt_path is None:
        prompt_ref = manifest.get("system_prompt_ref", "system_prompt.md")
        prompt_path = manifest_path.parent / prompt_ref

    prompt_text = ""
    if prompt_path and prompt_path.exists():
        try:
            prompt_text = prompt_path.read_text(encoding="utf-8")
        except Exception:
            findings.append(ContractFinding(
                rule_id="prompt-read-error", severity="warn",
                agent_id=agent_id, location=str(prompt_path),
                message="Could not read system_prompt.md.",
            ))
    else:
        findings.append(ContractFinding(
            rule_id="prompt-missing", severity="error",
            agent_id=agent_id, location=str(prompt_path),
            message=f"system_prompt.md not found at {prompt_path}.",
        ))
        return findings  # no point running other checks

    # Run each checker
    findings.extend(_check_identity(manifest, prompt_text, agent_id))
    findings.extend(_check_tool_consistency(manifest, prompt_text, agent_id))
    findings.extend(_check_output_schema_field_refs(manifest, prompt_text, agent_id))
    findings.extend(_check_execution_mode(manifest, prompt_text, agent_id))
    findings.extend(_check_preload_references(manifest, prompt_text, agent_id))
    findings.extend(_check_memory_tier_consistency(manifest, prompt_text, agent_id))
    return findings


def validate_domain_contracts(domain_root: Path) -> ContractReport:
    """Walk `{domain}/agents/**/agent_manifest.yaml` and validate each pair."""
    report = ContractReport()
    agents_dir = Path(domain_root) / "agents"
    if not agents_dir.is_dir():
        return report
    for manifest_path in sorted(agents_dir.rglob("agent_manifest.yaml")):
        report.agents_checked += 1
        report.findings.extend(validate_agent_contract(manifest_path))
    return report


# ── Rule checkers ─────────────────────────────────────────────────────────


_IDENTITY_RE = re.compile(r"[Yy]ou are\s+`?\*?\*?(?P<name>[A-Z][A-Za-z0-9_]+)`?\*?\*?")


def _check_identity(
    manifest: dict[str, Any], prompt: str, agent_id: str,
) -> list[ContractFinding]:
    """Prompt's 'You are X' line should match manifest.id's short name."""
    m = _IDENTITY_RE.search(prompt)
    if not m:
        return []
    prompt_name = m.group("name")
    # Extract short name from the agent id: `_genesis/TriageAgent/v1` → `TriageAgent`
    parts = agent_id.split("/")
    short_name = parts[-2] if len(parts) >= 2 else agent_id
    if prompt_name != short_name:
        return [ContractFinding(
            rule_id="identity-mismatch",
            severity="error",
            agent_id=agent_id,
            location="prompt:You are",
            message=f"Prompt claims `You are {prompt_name}` but manifest id is `{agent_id}`.",
            evidence=m.group(0),
        )]
    return []


# Tools recognized as framework-native — extend as new built-ins ship.
# NB: `submit_output` is intentionally NOT here — it's provider-injected
# (AnthropicProvider adds it when output_schema is set), so prompts
# referencing it should never be flagged as "undeclared tool."
_KNOWN_NATIVE_TOOLS = {
    "file_read", "file_write", "file_edit", "file_list",
    "list_dir", "search_files", "search_code", "shell_exec",
    "fetch_url",
    # Memory tiers (1.5 → 4)
    "list_paths",        # Tier 1.5 — file tree browse
    "context_retrieve",  # Tier 2   — keyword-indexed chunks
    "origin_fetch",      # Tier 3   — live origin re-fetch
    "memory_recall",     # Tier 4   — past run memory
}

# Generic backtick-quoted words to IGNORE (they aren't tool calls).
_GENERIC_IDENTIFIERS = {
    "id", "name", "type", "description", "content", "status", "output",
    "input", "tools", "credentials", "required", "optional", "path",
    "files", "keywords", "topic", "summary", "purpose",
    "submit_output",  # provider-injected; not declared in tools[]
}


def _check_tool_consistency(
    manifest: dict[str, Any], prompt: str, agent_id: str,
) -> list[ContractFinding]:
    """Every tool named in backticks in the prompt should be declared OR be a
    generic identifier. Flags references to tools the manifest doesn't declare.
    """
    declared_tools: set[str] = set()
    for t in manifest.get("tools") or []:
        if isinstance(t, dict):
            name = t.get("name") or t.get("id") or ""
        else:
            name = str(t)
        name = str(name).removeprefix("tool://").strip()
        if name:
            declared_tools.add(name)

    # Tokens of the form `foo_bar` in prompt (snake_case — our tool naming convention).
    # We only flag names that either match a known framework tool or look like
    # a declared tool in a sibling pack.
    mentioned = set(re.findall(r"`([a-z][a-z0-9_]{2,})\s*\(", prompt))  # `tool_name(`
    mentioned |= set(re.findall(r"`([a-z][a-z0-9_]{2,})`", prompt))     # `tool_name`
    mentioned -= _GENERIC_IDENTIFIERS

    findings: list[ContractFinding] = []
    for name in mentioned:
        # Only flag if it's a known framework tool OR looks like a tool (has underscore).
        # Avoids false positives on domain vocabulary.
        if name in _KNOWN_NATIVE_TOOLS and name not in declared_tools:
            findings.append(ContractFinding(
                rule_id="prompt-mentions-undeclared-tool",
                severity="warn",
                agent_id=agent_id,
                location="manifest.tools",
                message=f"Prompt references `{name}` but it's not in manifest.tools[].",
                evidence=name,
            ))
    return findings


def _check_output_schema_field_refs(
    manifest: dict[str, Any], prompt: str, agent_id: str,
) -> list[ContractFinding]:
    """Prompt references like `output.X` or `output.X.Y` should resolve in
    the manifest's output_schema.properties."""
    schema = manifest.get("output_schema") or {}
    if not isinstance(schema, dict):
        return []
    properties = (schema.get("properties") or {})
    if not isinstance(properties, dict):
        return []

    # Find `output.X` or output.X.Y references
    refs = set(re.findall(r"output\.([A-Za-z_][A-Za-z0-9_\.]*)", prompt))
    findings: list[ContractFinding] = []
    for ref in refs:
        first_segment = ref.split(".")[0]
        if first_segment not in properties:
            findings.append(ContractFinding(
                rule_id="prompt-refs-undeclared-output-field",
                severity="warn",
                agent_id=agent_id,
                location=f"output_schema.{first_segment}",
                message=f"Prompt references `output.{ref}` but `{first_segment}` "
                        "is not in output_schema.properties.",
                evidence=f"output.{ref}",
            ))
    return findings


_SINGLE_SHOT_PATTERNS = [
    r"single[- ]turn",
    r"single[- ]shot",
    r"\bone[- ]turn\b",
    r"emit.*in one call",
    r"one submit_output call",
    r"do NOT call.*tools",
]


def _check_execution_mode(
    manifest: dict[str, Any], prompt: str, agent_id: str,
) -> list[ContractFinding]:
    """If the prompt promises single-shot behavior, max_react_steps should be 1."""
    claims_single = any(re.search(pat, prompt, re.IGNORECASE) for pat in _SINGLE_SHOT_PATTERNS)
    if not claims_single:
        return []
    exec_mode = manifest.get("execution_mode") or {}
    if isinstance(exec_mode, dict):
        max_steps = exec_mode.get("max_react_steps") or exec_mode.get("max_steps")
    else:
        max_steps = None
    if max_steps is None:
        return []
    try:
        n = int(max_steps)
    except (TypeError, ValueError):
        return []
    if n > 1:
        return [ContractFinding(
            rule_id="prompt-claims-single-shot-but-max-steps-gt-1",
            severity="warn",
            agent_id=agent_id,
            location="execution_mode.max_react_steps",
            message=f"Prompt claims single-turn operation but max_react_steps={n}.",
        )]
    return []


def _check_preload_references(
    manifest: dict[str, Any], prompt: str, agent_id: str,
) -> list[ContractFinding]:
    """Preload sources named in the prompt should be in context.preload."""
    declared_preloads: set[str] = set()
    ctx = manifest.get("context") or {}
    if isinstance(ctx, dict):
        for p in ctx.get("preload") or []:
            if isinstance(p, str):
                declared_preloads.add(p)

    # Find `preload:foo_bar` or "preload source: foo_bar" references
    mentioned = set(re.findall(r"preload:([a-z_][a-z0-9_]*)", prompt))
    mentioned |= set(re.findall(
        r"preload(?:ed)?\s+(?:context\s+)?source[: ]+`?([a-z_][a-z0-9_]*)`?",
        prompt, re.IGNORECASE,
    ))

    findings: list[ContractFinding] = []
    for name in mentioned:
        if name not in declared_preloads and name in _KNOWN_PRELOAD_NAMES:
            findings.append(ContractFinding(
                rule_id="prompt-references-undeclared-preload",
                severity="warn",
                agent_id=agent_id,
                location="context.preload",
                message=f"Prompt references preload `{name}` but it's not in context.preload[].",
                evidence=name,
            ))
    return findings


# Known preload names (auto-update as new sources land in manifest_loader).
_KNOWN_PRELOAD_NAMES = {
    "tool_pack_catalog",
    "shadow_gentcore_builtin_tools",
    "domain_context_docs",
    "shared_stage_catalog",
    "capabilities_config",
    "known_mcp_servers",
    "tool_security_policy",
    "project_file_tree",   # Tier 1.5 file-tree map
}


def _check_memory_tier_consistency(
    manifest: dict[str, Any], prompt: str, agent_id: str,
) -> list[ContractFinding]:
    """If prompt teaches Tier 2/3 memory tools, manifest must declare them."""
    declared_tools: set[str] = set()
    for t in manifest.get("tools") or []:
        if isinstance(t, dict):
            name = t.get("name") or ""
        else:
            name = str(t)
        declared_tools.add(str(name).removeprefix("tool://"))

    findings: list[ContractFinding] = []

    tier2_taught = bool(re.search(r"context_retrieve\s*\(", prompt))
    if tier2_taught and "context_retrieve" not in declared_tools:
        findings.append(ContractFinding(
            rule_id="memory-tier2-tool-not-declared",
            severity="warn",
            agent_id=agent_id,
            location="manifest.tools",
            message="Prompt teaches Tier 2 retrieval but `context_retrieve` is not in tools.",
        ))

    tier3_taught = bool(re.search(r"origin_fetch\s*\(", prompt))
    if tier3_taught and "origin_fetch" not in declared_tools:
        findings.append(ContractFinding(
            rule_id="memory-tier3-tool-not-declared",
            severity="warn",
            agent_id=agent_id,
            location="manifest.tools",
            message="Prompt teaches Tier 3 origin_fetch but the tool is not declared.",
        ))

    tier4_taught = bool(re.search(r"memory_recall\s*\(", prompt))
    if tier4_taught and "memory_recall" not in declared_tools:
        findings.append(ContractFinding(
            rule_id="memory-tier4-tool-not-declared",
            severity="warn",
            agent_id=agent_id,
            location="manifest.tools",
            message="Prompt teaches Tier 4 memory_recall but the tool is not declared.",
        ))

    return findings
