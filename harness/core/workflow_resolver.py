"""WorkflowResolver — merge user-declared overrides with KnowledgeMapper auto-discovery.

Resolution order (highest priority wins):
  1. `domain.workflows.processes` set explicitly → use verbatim (full override).
  2. `domain.workflows.add_processes` / `exclude_processes` → apply deltas
     to auto-discovered list.
  3. Neither → full auto-discovery from KnowledgeMapper.
  4. If auto-discovery produced fewer than `MIN_PROCESSES` entries (or all
     below `CONFIDENCE_THRESHOLD`), fall back to stack-based defaults.

The resolver returns:
  * The final ordered list of process names.
  * A human-readable report describing what was chosen, the confidence of
    each, and the signal sources. This is printed by the CLI so users can
    see what to override.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


CONFIDENCE_THRESHOLD = 0.5
MIN_PROCESSES = 3

# Stack-based defaults when auto-discovery can't produce a reasonable list
# (greenfield repos, opaque signals, etc.). Keyed by `domain.industry` or
# `knowledge_map.stack`. Falls through to "generic".
STACK_DEFAULTS: dict[str, list[str]] = {
    "backend-api":        ["feature_delivery", "bug_fix", "refactor", "docs_refresh"],
    "backend-python":     ["feature_delivery", "bug_fix", "refactor", "docs_refresh"],
    "frontend-web":       ["feature_delivery", "bug_fix", "perf_investigation", "accessibility_audit"],
    "frontend-react":     ["feature_delivery", "bug_fix", "perf_investigation", "accessibility_audit"],
    "aws-ops":            ["incident_triage", "runbook_execution", "capacity_review", "cost_optimization"],
    "k8s-ops":            ["incident_triage", "runbook_execution", "capacity_review", "policy_review"],
    "healthcare-triage":  ["incident_triage", "compliance_review", "docs_refresh"],
    "fintech-ops":        ["incident_triage", "compliance_review", "audit_log_review", "docs_refresh"],
    "generic":            ["feature_delivery", "bug_fix", "refactor", "docs_refresh"],
}


@dataclass
class ResolvedProcess:
    """One resolved workflow process with provenance."""

    name: str
    confidence: float | str  # float for auto-discovered; "default" or "override" otherwise
    signals: list[str] = field(default_factory=list)
    source: str = "auto"  # "override", "delta", "auto", "default"


@dataclass
class ResolutionReport:
    """What the resolver decided, for CLI presentation."""

    processes: list[ResolvedProcess]
    triage_override: dict[str, Any] | None = None  # when user declared workflows.triage
    source_summary: str = ""  # e.g. "full override", "auto + 1 addition", "stack default"

    @property
    def process_names(self) -> list[str]:
        return [p.name for p in self.processes]

    def format_cli(self) -> str:
        """Human-readable block the CLI prints after resolution."""
        lines = [
            f"Genesis resolved {len(self.processes)} workflow processes"
            f" ({self.source_summary}):",
        ]
        for p in self.processes:
            conf = p.confidence if isinstance(p.confidence, str) else f"{p.confidence:.2f}"
            signals = ", ".join(p.signals) if p.signals else "—"
            lines.append(f"  - {p.name:<28} ({conf:<8} from {signals})")
        if self.triage_override:
            lines.append("  triage: user-declared in domain.yaml")
        lines.append(
            "Override via `workflows:` in domain.yaml. "
            "See docs/USER_GUIDE_END_TO_END.md §2.5."
        )
        return "\n".join(lines)


# ── Architect output normalization (back-compat shim) ────────────────────


def normalize_architect_output(architect_output: dict[str, Any]) -> dict[str, Any]:
    """Lift the deprecated singular `workflow_design` into `workflow_designs`.

    Pre-multi-workflow versions of AgentArchitectAgent emitted one
    `workflow_design: {...}` dict. The new schema expects
    `workflow_designs: [{...}]`. This shim accepts either shape and
    returns a dict where the plural is always present, so downstream
    Builder consumers + tests don't have to branch on both.

    Mutation-free: returns a shallow copy with the normalised key.
    """
    out = dict(architect_output)
    if "workflow_designs" not in out and isinstance(out.get("workflow_design"), dict):
        single = out["workflow_design"]
        # Preserve the original singular key for auditability but promote
        # the plural so Builder's loop works.
        out["workflow_designs"] = [single]
    return out


# ── Public API ────────────────────────────────────────────────────────────


def resolve_workflow_processes(
    *,
    domain_workflows: dict[str, Any] | None,
    discovered: Iterable[dict[str, Any]] | None,
    stack_key: str | None = None,
) -> ResolutionReport:
    """Return the final list of workflow processes to generate.

    Args:
        domain_workflows: the `workflows:` block from domain.yaml (already
            parsed into a dict). None / empty dict means no override.
        discovered: KnowledgeMapper's `workflow_processes` output — each
            entry should be a dict with keys like `name`, `confidence`
            (float), and `signals` (list of str). None/empty means no
            auto-discovery data is available.
        stack_key: the domain's industry or stack tag, used to look up
            STACK_DEFAULTS when auto-discovery is empty.

    Returns:
        A ResolutionReport with the resolved process list and provenance.
    """
    override = domain_workflows or {}
    explicit = override.get("processes")
    adds = list(override.get("add_processes") or [])
    excludes = set(override.get("exclude_processes") or [])
    triage = override.get("triage")

    # 1. Explicit full override — shortcut everything.
    if explicit:
        processes = [
            ResolvedProcess(name=name, confidence="override", source="override")
            for name in explicit
        ]
        return ResolutionReport(
            processes=processes,
            triage_override=triage,
            source_summary="full override from domain.yaml",
        )

    # 2. Start with auto-discovery, filter by confidence, apply deltas.
    auto: list[ResolvedProcess] = []
    for entry in discovered or []:
        name = entry.get("name") or entry.get("process") or ""
        if not name:
            continue
        confidence = entry.get("confidence", 0.0)
        signals = list(entry.get("signals") or [])
        try:
            conf_f = float(confidence)
        except (TypeError, ValueError):
            conf_f = 0.0
        if conf_f < CONFIDENCE_THRESHOLD:
            continue
        auto.append(
            ResolvedProcess(
                name=name,
                confidence=conf_f,
                signals=signals,
                source="auto",
            )
        )

    # Remove excluded
    filtered = [p for p in auto if p.name not in excludes]

    # Deduplicate adds against existing names, then append
    existing_names = {p.name for p in filtered}
    for add_name in adds:
        if add_name in existing_names:
            continue
        filtered.append(
            ResolvedProcess(
                name=add_name,
                confidence="override",
                source="delta",
                signals=["domain.yaml add_processes"],
            )
        )
        existing_names.add(add_name)

    # 3. If still below threshold count, fall back to stack defaults.
    if len(filtered) < MIN_PROCESSES:
        defaults = STACK_DEFAULTS.get(stack_key or "", STACK_DEFAULTS["generic"])
        for name in defaults:
            if name in existing_names or name in excludes:
                continue
            filtered.append(
                ResolvedProcess(
                    name=name,
                    confidence="default",
                    source="default",
                    signals=[f"stack default for {stack_key or 'generic'}"],
                )
            )
            existing_names.add(name)

    summary = _summarise_sources(filtered, has_deltas=bool(adds or excludes))
    return ResolutionReport(
        processes=filtered,
        triage_override=triage,
        source_summary=summary,
    )


def _summarise_sources(processes: list[ResolvedProcess], *, has_deltas: bool) -> str:
    counts: dict[str, int] = {}
    for p in processes:
        counts[p.source] = counts.get(p.source, 0) + 1
    parts: list[str] = []
    if counts.get("auto"):
        parts.append(f"{counts['auto']} auto-discovered")
    if counts.get("delta"):
        parts.append(f"{counts['delta']} user-added")
    if counts.get("default"):
        parts.append(f"{counts['default']} stack default")
    if has_deltas and "auto" in counts:
        parts.append("with deltas")
    return ", ".join(parts) or "empty"
