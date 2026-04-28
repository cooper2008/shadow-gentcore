"""Aggregate runtime signals for EvolutionAgent — the framework's Tier 5.

Over time a deployed domain emits three kinds of audit trails that
together tell the Evolution Agent *what to fix*:

  1. **Origin log** (`<domain>/.gentcore/origin_log.jsonl`) — every
     `origin_fetch()` call, with path + source + outcome. A path fetched
     12 times in 30 days is a signal that standards.md / reference chunks
     are missing content an agent keeps needing.

  2. **Citation reports** (embedded in run records / `_citation_report`
     blocks) — agents that consistently return `citation_score < 0.75`
     or emit `tier: none` on factual claims flag knowledge gaps on a
     specific tier.

  3. **Memory store** (`<domain>/.gentcore/memory/<agent>/memories.jsonl`)
     — repeated task signatures are candidates for template extraction
     (cheaper than re-synthesizing each time). Single-use tasks are
     background noise.

This module reduces those logs into a structured `EvolutionSignals`
bundle the Evolution agent consumes via a preload source — so it
doesn't need to iterate file_read over a hundred JSONL files.

Zero LLM calls, deterministic aggregation.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class OriginHotspot:
    """A path fetched repeatedly — candidate for chunk addition."""
    path: str
    source_uri: str
    fetch_count: int
    first_seen: float
    last_seen: float
    not_found_count: int = 0

    @property
    def priority_score(self) -> float:
        """Higher = more urgent. Count × recency × not-found-rate."""
        recency_days = max(1.0, (time.time() - self.last_seen) / 86400)
        recency_weight = 0.5 ** (recency_days / 30)  # 30-day half-life
        not_found_penalty = 1.0 + (self.not_found_count / max(1, self.fetch_count))
        return float(self.fetch_count * recency_weight * not_found_penalty)


@dataclass
class CitationWeakness:
    """An agent that struggles with citation — gap flag per tier."""
    agent_id: str
    avg_score: float
    runs_observed: int
    under_cited_tiers: list[str]  # which tiers were most often missing


@dataclass
class MemoryPattern:
    """A repeated task signature — template candidate."""
    agent_id: str
    task_signature: str
    summary: str
    recurrence_count: int
    avg_success_score: float


@dataclass
class EvolutionSignals:
    """Aggregated signals bundle for EvolutionAgent."""
    domain_root: str = ""
    origin_hotspots: list[OriginHotspot] = field(default_factory=list)
    citation_weaknesses: list[CitationWeakness] = field(default_factory=list)
    memory_patterns: list[MemoryPattern] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)

    def format_markdown(self, top_n: int = 10) -> str:
        """Render as an LLM-friendly preload context block."""
        lines = [
            "# Evolution Signals (pre-aggregated runtime telemetry)",
            "",
            f"Domain: `{self.domain_root}`",
            f"Signal counts: {self.totals}",
            "",
        ]
        if self.origin_hotspots:
            lines.append("## Top Tier-3 origin hotspots")
            lines.append("")
            lines.append("Paths re-fetched repeatedly — candidates for new chunks or standards additions.")
            lines.append("")
            lines.append("| Path | Source | Fetches | 404s | Priority |")
            lines.append("| --- | --- | --- | --- | --- |")
            hotspots = sorted(self.origin_hotspots, key=lambda h: -h.priority_score)[:top_n]
            for h in hotspots:
                lines.append(f"| `{h.path}` | `{h.source_uri[:40]}` | {h.fetch_count} | {h.not_found_count} | {h.priority_score:.2f} |")
            lines.append("")
        if self.citation_weaknesses:
            lines.append("## Citation weaknesses")
            lines.append("")
            lines.append("Agents consistently falling below citation thresholds — prompt refinement candidates.")
            lines.append("")
            for cw in sorted(self.citation_weaknesses, key=lambda c: c.avg_score)[:top_n]:
                tiers_str = ", ".join(cw.under_cited_tiers) or "—"
                lines.append(f"- `{cw.agent_id}`: avg score {cw.avg_score:.2f} over {cw.runs_observed} runs; weak tiers: {tiers_str}")
            lines.append("")
        if self.memory_patterns:
            lines.append("## Repeated task patterns")
            lines.append("")
            lines.append("Same-signature tasks seen ≥3×. Candidates for templating (cheaper than LLM synthesis each time).")
            lines.append("")
            for mp in sorted(self.memory_patterns, key=lambda m: -m.recurrence_count)[:top_n]:
                lines.append(f"- `{mp.agent_id}`: {mp.recurrence_count}× — {mp.summary[:80]}")
            lines.append("")
        if not (self.origin_hotspots or self.citation_weaknesses or self.memory_patterns):
            lines.append("_No significant runtime signals captured yet — domain may be newly deployed."
                         " Evolution proposals should flag this and recommend 30 more days of runs._")
        return "\n".join(lines)


def _read_origin_log(domain_root: Path) -> list[dict[str, Any]]:
    """Read origin_fetch audit lines from `.gentcore/origin_log.jsonl`."""
    log = domain_root / ".gentcore" / "origin_log.jsonl"
    if not log.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        with log.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        logger.warning("origin_log read failed for %s: %s", domain_root, exc)
    return entries


def _aggregate_origin_hotspots(entries: list[dict[str, Any]], min_fetches: int = 2) -> list[OriginHotspot]:
    """Count paths fetched ≥min_fetches times → hotspots."""
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for e in entries:
        key = (str(e.get("path", "")), str(e.get("source_uri", "")))
        if not key[0]:
            continue
        b = buckets.setdefault(key, {
            "first": float(e.get("timestamp", 0)),
            "last": float(e.get("timestamp", 0)),
            "count": 0,
            "nf": 0,
        })
        b["count"] += 1
        ts = float(e.get("timestamp", 0))
        b["first"] = min(b["first"], ts) if b["first"] > 0 else ts
        b["last"] = max(b["last"], ts)
        if e.get("outcome") == "not_found":
            b["nf"] += 1

    hotspots: list[OriginHotspot] = []
    for (path, src), b in buckets.items():
        if b["count"] >= min_fetches:
            hotspots.append(OriginHotspot(
                path=path, source_uri=src,
                fetch_count=b["count"], first_seen=b["first"], last_seen=b["last"],
                not_found_count=b["nf"],
            ))
    return hotspots


def _aggregate_memory_patterns(
    domain_root: Path, min_recurrence: int = 3,
) -> list[MemoryPattern]:
    """Walk `.gentcore/memory/<agent>/memories.jsonl` files — group by task_signature."""
    mem_root = domain_root / ".gentcore" / "memory"
    if not mem_root.is_dir():
        return []
    patterns: dict[tuple[str, str], dict[str, Any]] = {}
    for agent_dir in mem_root.iterdir():
        if not agent_dir.is_dir():
            continue
        log = agent_dir / "memories.jsonl"
        if not log.exists():
            continue
        try:
            with log.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue
                    sig = str(e.get("key", ""))
                    val = str(e.get("value", ""))
                    key = (agent_dir.name, sig)
                    b = patterns.setdefault(key, {
                        "count": 0, "summary": val[:200], "scores": [],
                    })
                    b["count"] += 1
                    md = e.get("metadata") or {}
                    if isinstance(md, dict) and "score" in md:
                        try:
                            b["scores"].append(float(md["score"]))
                        except (TypeError, ValueError):
                            pass
        except Exception as exc:
            logger.warning("memory log read failed for %s: %s", agent_dir, exc)
    out: list[MemoryPattern] = []
    for (agent, sig), b in patterns.items():
        if b["count"] < min_recurrence:
            continue
        avg = sum(b["scores"]) / len(b["scores"]) if b["scores"] else 0.0
        out.append(MemoryPattern(
            agent_id=agent, task_signature=sig,
            summary=b["summary"], recurrence_count=b["count"],
            avg_success_score=avg,
        ))
    return out


def _aggregate_citation_weaknesses(
    run_records: list[dict[str, Any]] | None,
) -> list[CitationWeakness]:
    """From supplied run records (each containing _citation_report), flag
    agents whose avg score falls below 0.75 over ≥3 runs."""
    if not run_records:
        return []
    by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in run_records:
        aid = str(rec.get("agent_id", "")) or str(rec.get("id", ""))
        if not aid:
            continue
        report = rec.get("_citation_report") or {}
        if isinstance(report, dict):
            by_agent[aid].append(report)

    weak: list[CitationWeakness] = []
    for aid, reports in by_agent.items():
        if len(reports) < 3:
            continue
        scores = [float(r.get("score", 1.0)) for r in reports]
        avg = sum(scores) / len(scores)
        if avg >= 0.75:
            continue
        # Find most common "under_cited" tiers
        missing_tiers: Counter[str] = Counter()
        for r in reports:
            for f in r.get("findings", []) or []:
                msg = str(f.get("message", ""))
                if "tier" in msg.lower():
                    # Regex would be nicer; substring is fine here
                    for tier in ("T1", "T1.5", "T2", "T3", "T4"):
                        if f" {tier} " in f" {msg} " or f"tier {tier}" in msg:
                            missing_tiers[tier] += 1
        weak.append(CitationWeakness(
            agent_id=aid,
            avg_score=avg,
            runs_observed=len(reports),
            under_cited_tiers=[t for t, _ in missing_tiers.most_common(3)],
        ))
    return weak


def gather_evolution_signals(
    domain_root: Path | str,
    *,
    run_records: list[dict[str, Any]] | None = None,
    min_origin_fetches: int = 2,
    min_memory_recurrence: int = 3,
) -> EvolutionSignals:
    """Single entry point. Aggregates all three signal types for a domain."""
    root = Path(domain_root).expanduser().resolve()
    origin_entries = _read_origin_log(root)
    hotspots = _aggregate_origin_hotspots(origin_entries, min_fetches=min_origin_fetches)
    memory_patterns = _aggregate_memory_patterns(root, min_recurrence=min_memory_recurrence)
    citation_weak = _aggregate_citation_weaknesses(run_records)

    return EvolutionSignals(
        domain_root=str(root),
        origin_hotspots=hotspots,
        citation_weaknesses=citation_weak,
        memory_patterns=memory_patterns,
        totals={
            "origin_events_total": len(origin_entries),
            "origin_hotspots_flagged": len(hotspots),
            "memory_patterns_flagged": len(memory_patterns),
            "citation_weaknesses_flagged": len(citation_weak),
            "run_records_scanned": len(run_records) if run_records else 0,
        },
    )
