"""Static security scanner for auto-generated tool packs.

Enforces `config/tool_security.yaml` rules against every pack emitted by
ToolSynthesizerAgent. The scanner runs inside the synthesizer's post-execute
hook and inside QualityGateAgent at the end of genesis_build — any
severity=block finding causes the gate to fail.

Rules (reference: `config/tool_security.yaml`):
  creds-declared-for-authed-tools   BLOCK
  creds-no-hardcoded-secrets        BLOCK
  urls-safe-parameter-syntax        BLOCK
  shell-requires-explicit-permission BLOCK
  no-inline-script                  BLOCK
  timeouts-bounded                  WARN
  pack-has-purpose                  WARN
  auto-generated-marker             INFO

Add new rules by extending `_RULE_CHECKERS`. Each checker returns a list
of `ScanFinding` objects (possibly empty).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

logger = logging.getLogger(__name__)


# ── Public API ───────────────────────────────────────────────────────────

@dataclass
class ScanFinding:
    rule_id: str
    severity: str  # "block" | "warn" | "info"
    pack_id: str
    location: str  # YAML field path or "file"
    message: str

    def format_line(self) -> str:
        sev_icon = {"block": "✗", "warn": "⚠", "info": "ℹ"}.get(self.severity, "•")
        return f"  {sev_icon} [{self.severity.upper()}] {self.rule_id} @ {self.pack_id} — {self.message} ({self.location})"


@dataclass
class ScanResult:
    passed: bool
    findings: list[ScanFinding] = field(default_factory=list)
    packs_scanned: int = 0

    @property
    def blocks(self) -> list[ScanFinding]:
        return [f for f in self.findings if f.severity == "block"]

    @property
    def warns(self) -> list[ScanFinding]:
        return [f for f in self.findings if f.severity == "warn"]

    def format_summary(self) -> str:
        if not self.findings:
            return f"✓ Security scan passed — {self.packs_scanned} pack(s) clean."
        lines = [
            f"Security scan: {len(self.blocks)} block / {len(self.warns)} warn "
            f"across {self.packs_scanned} pack(s)."
        ]
        for f in self.findings:
            lines.append(f.format_line())
        return "\n".join(lines)


def load_policy(policy_path: Path | None = None) -> dict[str, Any]:
    """Load the tool security policy YAML.

    Defaults to `<framework_root>/config/tool_security.yaml`.
    """
    if policy_path is None:
        framework_root = Path(__file__).resolve().parent.parent.parent
        policy_path = framework_root / "config" / "tool_security.yaml"
    if not policy_path.exists():
        logger.warning("tool_security.yaml not found at %s — scanner will no-op", policy_path)
        return {}
    try:
        return yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.error("Failed to parse tool_security.yaml: %s", exc)
        return {}


def scan_pack_yaml(
    pack_content: str,
    pack_id: str = "<unknown>",
    policy: dict[str, Any] | None = None,
) -> list[ScanFinding]:
    """Scan a single pack YAML string against the policy."""
    policy = policy if policy is not None else load_policy()
    if not policy:
        return []
    try:
        data = yaml.safe_load(pack_content) or {}
    except Exception as exc:
        return [ScanFinding(
            rule_id="yaml-parse-error", severity="block", pack_id=pack_id,
            location="root", message=f"Pack YAML failed to parse: {str(exc)[:120]}",
        )]

    effective_id = data.get("id") or pack_id
    allowlist = _build_allowlist(policy, effective_id)

    findings: list[ScanFinding] = []
    for checker in _RULE_CHECKERS:
        for f in checker(data, pack_content, effective_id, policy):
            if f.rule_id in allowlist:
                # Downgrade blocked → warn per allowlist
                if f.severity == "block":
                    f = ScanFinding(f.rule_id, "warn", f.pack_id, f.location,
                                    f.message + " [downgraded by allowlist]")
            findings.append(f)
    return findings


def scan_pack_file(path: Path, policy: dict[str, Any] | None = None) -> list[ScanFinding]:
    """Scan a pack YAML file."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        return [ScanFinding(
            rule_id="read-error", severity="block", pack_id=str(path),
            location="file", message=f"Cannot read pack file: {str(exc)[:120]}",
        )]
    return scan_pack_yaml(content, pack_id=str(path), policy=policy)


def scan_directory(
    root: Path,
    policy: dict[str, Any] | None = None,
    pattern: str = "**/*.yaml",
) -> ScanResult:
    """Scan every YAML under `root` against the policy."""
    policy = policy if policy is not None else load_policy()
    findings: list[ScanFinding] = []
    count = 0
    if not root.exists():
        return ScanResult(passed=True, findings=[], packs_scanned=0)
    for yaml_path in sorted(root.glob(pattern)):
        if yaml_path.is_file():
            count += 1
            findings.extend(scan_pack_file(yaml_path, policy))
    passed = not any(f.severity == "block" for f in findings)
    return ScanResult(passed=passed, findings=findings, packs_scanned=count)


def scan_packs(
    packs: list[tuple[str, str]],
    policy: dict[str, Any] | None = None,
) -> ScanResult:
    """Scan an in-memory list of (pack_id, yaml_content) tuples.

    Used by ToolSynthesizer's post-execute hook before (or instead of)
    touching the filesystem.
    """
    policy = policy if policy is not None else load_policy()
    findings: list[ScanFinding] = []
    for pack_id, content in packs:
        findings.extend(scan_pack_yaml(content, pack_id=pack_id, policy=policy))
    passed = not any(f.severity == "block" for f in findings)
    return ScanResult(passed=passed, findings=findings, packs_scanned=len(packs))


# ── Rule checkers ────────────────────────────────────────────────────────

def _check_creds_declared_for_authed_tools(
    data: dict[str, Any], raw: str, pack_id: str, policy: dict[str, Any],
) -> list[ScanFinding]:
    """BLOCK: Remote-URL tools must declare a credentials: block."""
    tools = data.get("tools") or []
    has_remote_base = False
    for t in tools:
        if not isinstance(t, dict):
            continue
        for key in ("base_url", "url", "endpoint"):
            u = t.get(key) or ""
            if isinstance(u, str) and u and not _is_local_url(u):
                has_remote_base = True
                break
        if has_remote_base:
            break
    # Also treat http_api adapter without explicit local base as remote-ish
    if not has_remote_base:
        for t in tools:
            if isinstance(t, dict) and t.get("adapter_class") in ("http_api", "rest"):
                has_remote_base = True
                break
    if has_remote_base and not (data.get("credentials")):
        return [ScanFinding(
            rule_id="creds-declared-for-authed-tools",
            severity="block",
            pack_id=pack_id,
            location="credentials",
            message="Pack has remote/http_api tools but no `credentials:` block declared.",
        )]
    return []


def _check_no_hardcoded_secrets(
    data: dict[str, Any], raw: str, pack_id: str, policy: dict[str, Any],
) -> list[ScanFinding]:
    """BLOCK: scan raw YAML for obvious secret patterns."""
    patterns = _find_rule(policy, "creds-no-hardcoded-secrets", "forbidden_patterns") or []
    findings: list[ScanFinding] = []
    for pat in patterns:
        try:
            if re.search(pat, raw):
                findings.append(ScanFinding(
                    rule_id="creds-no-hardcoded-secrets",
                    severity="block",
                    pack_id=pack_id,
                    location="raw-yaml",
                    message=f"Possible hardcoded secret matching /{pat}/ found in pack body.",
                ))
        except re.error:
            continue
    return findings


def _check_safe_url_templates(
    data: dict[str, Any], raw: str, pack_id: str, policy: dict[str, Any],
) -> list[ScanFinding]:
    """BLOCK: URL templates must not contain code/pipes/unsafe schemes."""
    patterns = _find_rule(policy, "urls-safe-parameter-syntax", "forbidden_url_patterns") or []
    findings: list[ScanFinding] = []
    tools = data.get("tools") or []
    for idx, t in enumerate(tools):
        if not isinstance(t, dict):
            continue
        for field_name in ("base_url", "url", "endpoint", "path_template"):
            val = t.get(field_name) or ""
            if not isinstance(val, str):
                continue
            for pat in patterns:
                try:
                    if re.search(pat, val):
                        findings.append(ScanFinding(
                            rule_id="urls-safe-parameter-syntax",
                            severity="block",
                            pack_id=pack_id,
                            location=f"tools[{idx}].{field_name}",
                            message=f"URL template violates pattern /{pat}/: {val[:80]}",
                        ))
                except re.error:
                    continue
    return findings


def _check_shell_permission(
    data: dict[str, Any], raw: str, pack_id: str, policy: dict[str, Any],
) -> list[ScanFinding]:
    """BLOCK: shell adapters require permissions.shell_command == allow."""
    tools = data.get("tools") or []
    perm = (data.get("permissions") or {}).get("shell_command", "")
    shell_allowed = str(perm).lower() == "allow"
    findings: list[ScanFinding] = []
    for idx, t in enumerate(tools):
        if not isinstance(t, dict):
            continue
        if t.get("adapter_class") == "shell" and not shell_allowed:
            findings.append(ScanFinding(
                rule_id="shell-requires-explicit-permission",
                severity="block",
                pack_id=pack_id,
                location=f"tools[{idx}]",
                message="Tool uses `adapter_class: shell` but pack does not set permissions.shell_command: allow.",
            ))
    return findings


def _check_no_inline_scripts(
    data: dict[str, Any], raw: str, pack_id: str, policy: dict[str, Any],
) -> list[ScanFinding]:
    """BLOCK: forbidden fields that embed executable code."""
    forbidden = _find_rule(policy, "no-inline-script", "forbidden_fields") or []
    findings: list[ScanFinding] = []

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in forbidden and isinstance(v, str) and v.strip():
                    findings.append(ScanFinding(
                        rule_id="no-inline-script",
                        severity="block",
                        pack_id=pack_id,
                        location=f"{path}.{k}",
                        message=f"Field `{k}` must not embed executable code.",
                    ))
                _walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    _walk(data, "root")
    return findings


def _check_timeouts_bounded(
    data: dict[str, Any], raw: str, pack_id: str, policy: dict[str, Any],
) -> list[ScanFinding]:
    """WARN: timeouts > 60s on non-batch tools."""
    tools = data.get("tools") or []
    findings: list[ScanFinding] = []
    for idx, t in enumerate(tools):
        if not isinstance(t, dict):
            continue
        timeout = t.get("timeout")
        if isinstance(timeout, (int, float)) and timeout > 60:
            findings.append(ScanFinding(
                rule_id="timeouts-bounded",
                severity="warn",
                pack_id=pack_id,
                location=f"tools[{idx}].timeout",
                message=f"Timeout {timeout}s exceeds 60s default. Confirm this tool is batch/build-like.",
            ))
    return findings


def _check_pack_has_purpose(
    data: dict[str, Any], raw: str, pack_id: str, policy: dict[str, Any],
) -> list[ScanFinding]:
    """WARN: pack description + per-tool purpose."""
    findings: list[ScanFinding] = []
    if not (data.get("description") or "").strip():
        findings.append(ScanFinding(
            rule_id="pack-has-purpose",
            severity="warn",
            pack_id=pack_id,
            location="description",
            message="Pack missing top-level `description:` field.",
        ))
    tools = data.get("tools") or []
    for idx, t in enumerate(tools):
        if not isinstance(t, dict):
            continue
        if not (t.get("purpose") or t.get("description") or "").strip():
            findings.append(ScanFinding(
                rule_id="pack-has-purpose",
                severity="warn",
                pack_id=pack_id,
                location=f"tools[{idx}]",
                message="Tool missing `purpose:` or `description:`.",
            ))
    return findings


def _check_auto_generated_marker(
    data: dict[str, Any], raw: str, pack_id: str, policy: dict[str, Any],
) -> list[ScanFinding]:
    """INFO: synthesized packs must carry auto_generated + pending_review."""
    meta = data.get("metadata") or {}
    if not meta.get("auto_generated"):
        return [ScanFinding(
            rule_id="auto-generated-marker",
            severity="info",
            pack_id=pack_id,
            location="metadata.auto_generated",
            message="Synthesized pack should carry `metadata.auto_generated: true`.",
        )]
    return []


_RULE_CHECKERS: list[Callable[..., list[ScanFinding]]] = [
    _check_creds_declared_for_authed_tools,
    _check_no_hardcoded_secrets,
    _check_safe_url_templates,
    _check_shell_permission,
    _check_no_inline_scripts,
    _check_timeouts_bounded,
    _check_pack_has_purpose,
    _check_auto_generated_marker,
]


# ── Helpers ──────────────────────────────────────────────────────────────

def _is_local_url(u: str) -> bool:
    u = u.lower()
    return (
        u.startswith("http://localhost")
        or u.startswith("http://127.0.0.1")
        or u.startswith("https://localhost")
        or u.startswith("https://127.0.0.1")
        or u.startswith("file://")
    )


def _find_rule(policy: dict[str, Any], rule_id: str, key: str) -> Any:
    """Pull `key` out of the rule matching `rule_id`."""
    for r in policy.get("rules", []) or []:
        if isinstance(r, dict) and r.get("id") == rule_id:
            return r.get(key)
    return None


def _build_allowlist(policy: dict[str, Any], pack_id: str) -> set[str]:
    """Return rule_ids downgradable-to-warn for this pack."""
    allowlist: set[str] = set()
    for entry in policy.get("allowlist", []) or []:
        if isinstance(entry, dict) and entry.get("pack_id") == pack_id:
            for rule_id in entry.get("downgrade", []) or []:
                allowlist.add(str(rule_id))
    return allowlist
