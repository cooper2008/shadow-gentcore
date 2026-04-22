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


async def validate_agent_contract_with_judge(
    manifest_path: Path,
    prompt_path: Path | None = None,
    *,
    provider: Any = None,
    model_hint: str | None = None,
) -> list[ContractFinding]:
    """Same as validate_agent_contract, plus an LLM-as-judge pass for
    semantic drift that regex misses.

    Cross-model review (Gemini 3.1 Pro + GPT-5.4) flagged identity
    regex as trivially fooled by "You are the TriageAgent equivalent
    of..." and tool-consistency regex as missing bare-mention drift.
    GPT's guidance was HYBRID — keep regex for structural invariants,
    add LLM judge as advisory overlay, treat judge findings as advisory
    unless corroborated by a structural mismatch.

    Args:
        manifest_path / prompt_path: same as validate_agent_contract.
        provider: LLM provider (Anthropic / OpenAI compat / Bedrock).
                  When None, skips the judge and returns regex results.
        model_hint: optional model override (e.g. "haiku-4-5" for cheap/fast).

    Returns regex findings + judge findings. Judge findings have
    severity="advisory" unless they corroborate a regex WARN (then
    upgraded to "warn").
    """
    regex_findings = validate_agent_contract(manifest_path, prompt_path)
    if provider is None:
        return regex_findings

    # Load manifest + prompt for the judge payload
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return regex_findings

    agent_id = str(manifest.get("id") or manifest_path.parent.parent.name)
    prompt_ref = manifest.get("system_prompt_ref", "system_prompt.md")
    resolved = prompt_path or (manifest_path.parent / prompt_ref)
    if not resolved.exists():
        return regex_findings
    prompt_text = resolved.read_text(encoding="utf-8")

    judge_findings = await _run_llm_judge(
        manifest=manifest, prompt=prompt_text, agent_id=agent_id,
        provider=provider, model_hint=model_hint,
    )

    # Corroboration upgrade: if a judge finding's underlying rule_id
    # matches a regex WARN/ERROR, upgrade the judge finding to "warn".
    # Otherwise leave it "advisory" so CI doesn't block on judge-alone
    # signals. Strip the `judge-` prefix before comparing — judge IDs are
    # prefixed for traceability but the underlying rule name is what
    # corroborates the regex check.
    regex_rules = {f.rule_id for f in regex_findings if f.severity in ("warn", "error")}
    upgraded: list[ContractFinding] = []
    for f in judge_findings:
        underlying = f.rule_id.removeprefix("judge-")
        if underlying in regex_rules or f.rule_id.startswith("judge-corroborated-"):
            upgraded.append(ContractFinding(
                rule_id=f.rule_id, severity="warn", agent_id=f.agent_id,
                location=f.location, message=f.message + " [corroborated]",
                evidence=f.evidence,
            ))
        else:
            upgraded.append(f)
    return regex_findings + upgraded


_JUDGE_SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["rule_id", "severity", "message"],
                "properties": {
                    "rule_id": {
                        "type": "string",
                        "description": "Short kebab-case id. Prefix with `judge-` to signal LLM origin.",
                    },
                    "severity": {"type": "string", "enum": ["advisory", "warn"]},
                    "location": {"type": "string", "description": "manifest field path or prompt excerpt"},
                    "message": {"type": "string", "description": "One-sentence finding explanation"},
                    "evidence": {"type": "string", "description": "≤80 char quote from manifest or prompt"},
                },
            },
        },
    },
}


_JUDGE_SYSTEM_PROMPT = """\
You are a **senior prompt-engineering reviewer** doing a semantic drift
check between an AI agent's `agent_manifest.yaml` and its `system_prompt.md`.

The structural regex checks have already run. Your job is to catch
drift those checks miss — subtler semantic mismatches that could cause
the agent to misbehave at runtime.

Focus on:
1. **Identity paraphrase** — does the prompt's persona (however phrased)
   match the manifest's agent ID / short name? Catch "You are the X
   equivalent of Y", "Act as X", "Your role is X" variants.
2. **Tool-usage intent** — does the prompt DESCRIBE a tool in prose
   without backticks that isn't in manifest.tools[]? E.g. prompt says
   "call jira search" but manifest lacks `jira_search`.
3. **Dead declared tools** — is any manifest.tools entry NEVER referenced
   or described in the prompt? It's dead weight.
4. **Output contract drift** — does the prompt describe output fields
   (in prose or examples) that aren't in output_schema.properties?
5. **Execution mode drift** — does the prompt implicitly require
   iterative calls (e.g. "iterate until X", "loop through Y") when the
   manifest declares max_react_steps=1?
6. **Memory tier mismatch** — does the prompt teach a decision ladder
   (Tier 1→2→3) without the corresponding tools declared?

**Rules for findings:**
- Each finding is `{rule_id, severity, location, message, evidence}`.
- severity="advisory" for subjective / partial drift.
- severity="warn" ONLY when you are confident this will cause a runtime failure.
- Prefix every rule_id with `judge-` (so it's traceable back to LLM output).
- Cite `evidence` — actual quote from manifest or prompt, max 80 chars.
- Return EMPTY `findings: []` if no semantic drift. Over-reporting is
  worse than under-reporting here.
- Output must be valid JSON matching the declared schema. Nothing else.
"""


async def _run_llm_judge(
    *,
    manifest: dict[str, Any],
    prompt: str,
    agent_id: str,
    provider: Any,
    model_hint: str | None = None,
) -> list[ContractFinding]:
    """Invoke a fast LLM as judge over (manifest, prompt). Returns findings."""
    import json as _json

    # Trim overly-verbose inputs — we only need structural + intent, not
    # the full doc body. Keeps judge cost low.
    manifest_summary = {
        k: v for k, v in manifest.items()
        if k in ("id", "description", "execution_mode", "tools",
                 "context", "input_schema", "output_schema", "permissions",
                 "required_credentials")
    }
    # Truncate prompt if large
    prompt_excerpt = prompt if len(prompt) < 8000 else prompt[:4000] + "\n...[...truncated...]...\n" + prompt[-2000:]

    user_msg = (
        f"Agent id: `{agent_id}`\n\n"
        f"### agent_manifest.yaml (abridged)\n\n```yaml\n"
        f"{yaml.safe_dump(manifest_summary, sort_keys=False, default_flow_style=False)}\n```\n\n"
        f"### system_prompt.md\n\n```markdown\n{prompt_excerpt}\n```\n\n"
        "Return findings as JSON matching the declared output_schema."
    )
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    chat_kwargs: dict[str, Any] = {"output_schema": _JUDGE_SCHEMA}
    if model_hint:
        chat_kwargs["model"] = model_hint

    try:
        resp = await provider.chat(messages, **chat_kwargs)
    except Exception as exc:
        logger.warning("LLM judge failed for %s: %s", agent_id, exc)
        return []

    content = getattr(resp, "content", None) or (resp.get("content", "") if isinstance(resp, dict) else "")
    if not content:
        return []
    try:
        parsed = _json.loads(content) if isinstance(content, str) else content
    except Exception as exc:
        logger.warning("LLM judge output unparseable for %s: %s", agent_id, exc)
        return []

    findings: list[ContractFinding] = []
    for entry in (parsed.get("findings") if isinstance(parsed, dict) else []) or []:
        if not isinstance(entry, dict):
            continue
        rule_id = str(entry.get("rule_id", "")).strip() or "judge-unspecified"
        if not rule_id.startswith("judge-"):
            rule_id = f"judge-{rule_id}"
        sev = str(entry.get("severity", "advisory")).strip().lower()
        if sev not in ("advisory", "warn"):
            sev = "advisory"
        findings.append(ContractFinding(
            rule_id=rule_id,
            severity=sev,
            agent_id=agent_id,
            location=str(entry.get("location", "prompt/manifest")),
            message=str(entry.get("message", ""))[:240],
            evidence=str(entry.get("evidence", ""))[:120],
        ))
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
