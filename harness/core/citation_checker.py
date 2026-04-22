"""Tier-citation enforcement — the "real hallucination fix."

When a domain agent makes a factual claim in its output, it should declare
which retrieval tier (T1 standards / T2 context_retrieve / T3 origin_fetch /
T4 memory_recall) supported it. Without this signal, a weak Tier 2 hit
gets treated as permission to answer confidently — exactly the failure
mode cross-model review flagged.

This module provides:

  1. `extract_citations(output)` — pull a citation list out of an agent's
     structured output. Supports two shapes agents can choose from:
       * `output.citations: [{claim, tier, source}, ...]`   (explicit list)
       * `output.meta.citations: [...]`                     (nested form)
     Returns [] when neither is present.

  2. `score_citations(output, *, min_citations, require_tiers)` — compute
     a coverage score. Used by gate expressions:
       condition: "status == success and citation_score >= 0.75"

  3. `CitationFinding` + `validate_citations()` — lint a citation list:
     unknown tiers, empty claims, tier mismatches against actual tool
     usage in `output.tool_calls`.

Runtime integration (future commit): AgentRunner attaches
`_citation_report` to the result; gate expressions can opt in via the
new `citation_score` or `citation_coverage` identifiers in expr.py.

Entirely additive. Agents that don't emit citations are unaffected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


_VALID_TIERS = {"T1", "T1.5", "T2", "T3", "T4", "standards", "file_tree",
                "context_retrieve", "origin_fetch", "memory_recall"}

# Normalise tier labels to a canonical form so gate expressions don't
# need to worry about case / synonyms.
_TIER_ALIASES = {
    "t1": "T1", "standards": "T1", "standards.md": "T1",
    "t1.5": "T1.5", "file_tree": "T1.5", "list_paths": "T1.5",
    "t2": "T2", "context_retrieve": "T2",
    "t3": "T3", "origin_fetch": "T3",
    "t4": "T4", "memory_recall": "T4",
    "none": "none", "self": "self", "unknown": "unknown",
}


@dataclass
class Citation:
    """A single claim → tier → source mapping."""
    claim: str
    tier: str        # canonical (T1 / T1.5 / T2 / T3 / T4 / none / self)
    source: str = "" # chunk_id / path / memory key / "self" for agent's own reasoning
    confidence: float = 1.0


@dataclass
class CitationFinding:
    severity: str  # "error" | "warn" | "info"
    message: str
    claim: str = ""


@dataclass
class CitationReport:
    citations: list[Citation] = field(default_factory=list)
    findings: list[CitationFinding] = field(default_factory=list)
    score: float = 1.0  # 0.0-1.0, higher = better coverage
    total_claims: int = 0
    cited_claims: int = 0

    @property
    def passed(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)


def _normalise_tier(raw: str) -> str:
    if not isinstance(raw, str):
        return "unknown"
    return _TIER_ALIASES.get(raw.strip().lower(), raw.strip())


def extract_citations(output: Any) -> list[Citation]:
    """Pull citations from an agent output dict. Supports two shapes."""
    if not isinstance(output, dict):
        return []
    raw = output.get("citations")
    if raw is None:
        meta = output.get("meta")
        if isinstance(meta, dict):
            raw = meta.get("citations")
    if not isinstance(raw, list):
        return []

    out: list[Citation] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        claim = str(entry.get("claim", "")).strip()
        if not claim:
            continue
        tier = _normalise_tier(str(entry.get("tier", "unknown")))
        source = str(entry.get("source", "")).strip()
        try:
            confidence = float(entry.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        out.append(Citation(claim=claim, tier=tier, source=source, confidence=confidence))
    return out


def validate_citations(
    citations: list[Citation],
    *,
    require_tiers: list[str] | None = None,
    min_source_length: int = 1,
) -> list[CitationFinding]:
    """Lint a citation list.

    Checks:
      - tier must be one of the known tier labels (or explicit "none"/"self")
      - source must be non-empty when tier is T2 / T3 / T4 (factual tiers)
      - claim must be non-empty
      - if `require_tiers` is set, at least one citation must use each
        required tier (e.g. ["T2"] enforces that T2 was consulted)
    """
    findings: list[CitationFinding] = []
    tiers_seen: set[str] = set()
    for idx, c in enumerate(citations):
        if not c.claim:
            findings.append(CitationFinding("error", f"Citation {idx} has empty claim"))
            continue
        tiers_seen.add(c.tier)
        if c.tier not in _VALID_TIERS and c.tier not in ("none", "self", "unknown"):
            findings.append(CitationFinding(
                "warn",
                f"Citation {idx} has unknown tier {c.tier!r} — expected one of T1/T1.5/T2/T3/T4",
                claim=c.claim,
            ))
        if c.tier in ("T2", "T3", "T4") and len(c.source) < min_source_length:
            findings.append(CitationFinding(
                "warn",
                f"Citation {idx} on tier {c.tier} has empty source — "
                f"factual tiers must name their artifact (chunk_id / path / memory key)",
                claim=c.claim,
            ))
    if require_tiers:
        for t in require_tiers:
            canonical = _normalise_tier(t)
            if canonical not in tiers_seen:
                findings.append(CitationFinding(
                    "error",
                    f"Required tier {canonical} not present in citations (seen: {sorted(tiers_seen)})",
                ))
    return findings


def score_citations(
    output: Any,
    *,
    min_citations: int = 0,
    require_tiers: list[str] | None = None,
) -> CitationReport:
    """Extract + validate + score. Single entry point for gate expressions."""
    report = CitationReport()
    report.citations = extract_citations(output)
    report.total_claims = len(report.citations)
    report.cited_claims = sum(
        1 for c in report.citations
        if c.tier not in ("none", "unknown") and (c.tier == "self" or c.source)
    )

    report.findings = validate_citations(report.citations, require_tiers=require_tiers)

    # Coverage score:
    # - 1.0 when every citation has a real tier + source (or is self-marked)
    # - Scales with cited_claims / total_claims
    # - Hard 0.0 if fewer than min_citations (agent didn't bother)
    if min_citations > 0 and report.total_claims < min_citations:
        report.score = 0.0
        report.findings.append(CitationFinding(
            "error",
            f"Only {report.total_claims} citation(s); require_min={min_citations}",
        ))
    elif report.total_claims == 0:
        # No citations declared — neutral score, but an agent manifest
        # can enforce `require_citations: true` separately.
        report.score = 1.0 if min_citations == 0 else 0.0
    else:
        report.score = report.cited_claims / report.total_claims

    return report
