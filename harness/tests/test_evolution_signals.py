"""Tests for evolution_signals aggregator.

Covers:
  * Origin hotspot detection + priority score
  * Memory pattern aggregation
  * Citation weakness detection across run records
  * Graceful handling of missing / malformed inputs
  * Markdown rendering shape + totals
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from harness.core.evolution_signals import (
    CitationWeakness,
    EvolutionSignals,
    MemoryPattern,
    OriginHotspot,
    gather_evolution_signals,
)


# ── Origin hotspots ──────────────────────────────────────────────────────


def _write_origin_log(root: Path, entries: list[dict]) -> None:
    gentcore = root / ".gentcore"
    gentcore.mkdir(parents=True, exist_ok=True)
    log = gentcore / "origin_log.jsonl"
    with log.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


class TestOriginHotspots:
    def test_path_fetched_below_threshold_not_flagged(self, tmp_path: Path) -> None:
        _write_origin_log(tmp_path, [
            {"path": "/docs/a.md", "source_uri": "github://acme/r", "timestamp": time.time()},
        ])
        signals = gather_evolution_signals(tmp_path, min_origin_fetches=2)
        assert signals.origin_hotspots == []

    def test_path_fetched_repeatedly_is_hotspot(self, tmp_path: Path) -> None:
        now = time.time()
        _write_origin_log(tmp_path, [
            {"path": "/docs/a.md", "source_uri": "github://acme/r", "timestamp": now - 10},
            {"path": "/docs/a.md", "source_uri": "github://acme/r", "timestamp": now - 5},
            {"path": "/docs/a.md", "source_uri": "github://acme/r", "timestamp": now},
        ])
        signals = gather_evolution_signals(tmp_path, min_origin_fetches=2)
        assert len(signals.origin_hotspots) == 1
        h = signals.origin_hotspots[0]
        assert h.path == "/docs/a.md"
        assert h.fetch_count == 3
        assert h.first_seen <= h.last_seen

    def test_not_found_outcomes_raise_priority(self, tmp_path: Path) -> None:
        now = time.time()
        _write_origin_log(tmp_path, [
            {"path": "/docs/miss.md", "source_uri": "github://a/r", "timestamp": now, "outcome": "not_found"},
            {"path": "/docs/miss.md", "source_uri": "github://a/r", "timestamp": now, "outcome": "not_found"},
            {"path": "/docs/hit.md",  "source_uri": "github://a/r", "timestamp": now},
            {"path": "/docs/hit.md",  "source_uri": "github://a/r", "timestamp": now},
        ])
        signals = gather_evolution_signals(tmp_path, min_origin_fetches=2)
        by_path = {h.path: h for h in signals.origin_hotspots}
        # not_found path should outrank the hit path given equal counts
        assert by_path["/docs/miss.md"].priority_score > by_path["/docs/hit.md"].priority_score
        assert by_path["/docs/miss.md"].not_found_count == 2

    def test_unreadable_log_returns_empty(self, tmp_path: Path) -> None:
        """Missing origin_log is not an error — newly deployed domain."""
        signals = gather_evolution_signals(tmp_path)
        assert signals.origin_hotspots == []

    def test_malformed_line_skipped(self, tmp_path: Path) -> None:
        (tmp_path / ".gentcore").mkdir()
        log = tmp_path / ".gentcore" / "origin_log.jsonl"
        log.write_text(
            json.dumps({"path": "/ok.md", "source_uri": "a", "timestamp": time.time()}) + "\n"
            + "not json at all\n"
            + json.dumps({"path": "/ok.md", "source_uri": "a", "timestamp": time.time()}) + "\n",
            encoding="utf-8",
        )
        signals = gather_evolution_signals(tmp_path, min_origin_fetches=2)
        assert len(signals.origin_hotspots) == 1
        assert signals.origin_hotspots[0].fetch_count == 2


# ── Memory patterns ──────────────────────────────────────────────────────


def _write_memory(root: Path, agent: str, entries: list[dict]) -> None:
    mem_dir = root / ".gentcore" / "memory" / agent
    mem_dir.mkdir(parents=True, exist_ok=True)
    log = mem_dir / "memories.jsonl"
    with log.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


class TestMemoryPatterns:
    def test_recurrence_below_threshold_not_flagged(self, tmp_path: Path) -> None:
        _write_memory(tmp_path, "TriageAgent", [
            {"key": "sig-1", "value": "first"},
            {"key": "sig-1", "value": "second"},
        ])
        signals = gather_evolution_signals(tmp_path, min_memory_recurrence=3)
        assert signals.memory_patterns == []

    def test_recurrence_above_threshold_flagged(self, tmp_path: Path) -> None:
        _write_memory(tmp_path, "TriageAgent", [
            {"key": "sig-A", "value": "val A", "metadata": {"score": 0.9}},
            {"key": "sig-A", "value": "val A", "metadata": {"score": 0.85}},
            {"key": "sig-A", "value": "val A", "metadata": {"score": 1.0}},
            {"key": "sig-B", "value": "val B"},
        ])
        signals = gather_evolution_signals(tmp_path, min_memory_recurrence=3)
        assert len(signals.memory_patterns) == 1
        mp = signals.memory_patterns[0]
        assert mp.agent_id == "TriageAgent"
        assert mp.task_signature == "sig-A"
        assert mp.recurrence_count == 3
        assert 0.9 < mp.avg_success_score <= 1.0

    def test_multiple_agents_tracked_independently(self, tmp_path: Path) -> None:
        _write_memory(tmp_path, "A", [{"key": "same", "value": "x"}] * 3)
        _write_memory(tmp_path, "B", [{"key": "same", "value": "x"}] * 3)
        signals = gather_evolution_signals(tmp_path, min_memory_recurrence=3)
        agent_ids = sorted(p.agent_id for p in signals.memory_patterns)
        assert agent_ids == ["A", "B"]

    def test_missing_memory_root_returns_empty(self, tmp_path: Path) -> None:
        signals = gather_evolution_signals(tmp_path)
        assert signals.memory_patterns == []


# ── Citation weaknesses ──────────────────────────────────────────────────


class TestCitationWeaknesses:
    def test_no_run_records_returns_empty(self, tmp_path: Path) -> None:
        signals = gather_evolution_signals(tmp_path, run_records=None)
        assert signals.citation_weaknesses == []

    def test_fewer_than_three_runs_not_flagged(self, tmp_path: Path) -> None:
        runs = [
            {"agent_id": "A", "_citation_report": {"score": 0.1, "findings": []}},
            {"agent_id": "A", "_citation_report": {"score": 0.1, "findings": []}},
        ]
        signals = gather_evolution_signals(tmp_path, run_records=runs)
        assert signals.citation_weaknesses == []

    def test_avg_above_threshold_not_flagged(self, tmp_path: Path) -> None:
        runs = [
            {"agent_id": "A", "_citation_report": {"score": 0.9, "findings": []}},
        ] * 4
        signals = gather_evolution_signals(tmp_path, run_records=runs)
        assert signals.citation_weaknesses == []

    def test_avg_below_threshold_flagged(self, tmp_path: Path) -> None:
        runs = [
            {"agent_id": "A", "_citation_report": {"score": 0.4, "findings": []}},
            {"agent_id": "A", "_citation_report": {"score": 0.5, "findings": []}},
            {"agent_id": "A", "_citation_report": {"score": 0.6, "findings": []}},
        ]
        signals = gather_evolution_signals(tmp_path, run_records=runs)
        assert len(signals.citation_weaknesses) == 1
        cw = signals.citation_weaknesses[0]
        assert cw.agent_id == "A"
        assert 0.4 <= cw.avg_score <= 0.6
        assert cw.runs_observed == 3

    def test_tier_mentions_extracted(self, tmp_path: Path) -> None:
        runs = [
            {
                "agent_id": "A",
                "_citation_report": {
                    "score": 0.3,
                    "findings": [
                        {"message": "Required tier T2 not cited"},
                        {"message": "Required tier T2 not cited"},
                        {"message": "Required tier T3 not cited"},
                    ],
                },
            },
        ] * 3
        signals = gather_evolution_signals(tmp_path, run_records=runs)
        cw = signals.citation_weaknesses[0]
        assert "T2" in cw.under_cited_tiers  # most common


# ── Totals + markdown rendering ──────────────────────────────────────────


class TestEvolutionSignalsBundle:
    def test_totals_count_everything(self, tmp_path: Path) -> None:
        _write_origin_log(tmp_path, [
            {"path": "/a.md", "source_uri": "x", "timestamp": time.time()},
            {"path": "/a.md", "source_uri": "x", "timestamp": time.time()},
        ])
        _write_memory(tmp_path, "Ag", [{"key": "k", "value": "v"}] * 3)
        runs = [{"agent_id": "A", "_citation_report": {"score": 0.1, "findings": []}}] * 3
        signals = gather_evolution_signals(tmp_path, run_records=runs)
        assert signals.totals["origin_events_total"] == 2
        assert signals.totals["origin_hotspots_flagged"] == 1
        assert signals.totals["memory_patterns_flagged"] == 1
        assert signals.totals["citation_weaknesses_flagged"] == 1
        assert signals.totals["run_records_scanned"] == 3

    def test_markdown_has_sections_when_populated(self, tmp_path: Path) -> None:
        _write_origin_log(tmp_path, [
            {"path": "/a.md", "source_uri": "x", "timestamp": time.time()},
            {"path": "/a.md", "source_uri": "x", "timestamp": time.time()},
        ])
        _write_memory(tmp_path, "Ag", [{"key": "k", "value": "v"}] * 3)
        runs = [{"agent_id": "A", "_citation_report": {"score": 0.1, "findings": []}}] * 3
        signals = gather_evolution_signals(tmp_path, run_records=runs)
        md = signals.format_markdown()
        assert "# Evolution Signals" in md
        assert "origin hotspots" in md.lower()
        assert "citation weaknesses" in md.lower()
        assert "task patterns" in md.lower()

    def test_markdown_empty_domain_says_so(self, tmp_path: Path) -> None:
        signals = gather_evolution_signals(tmp_path)
        md = signals.format_markdown()
        assert "No significant runtime signals" in md

    def test_domain_root_is_absolute_in_signals(self, tmp_path: Path) -> None:
        signals = gather_evolution_signals(tmp_path)
        assert Path(signals.domain_root).is_absolute()


# ── Dataclass units ──────────────────────────────────────────────────────


class TestOriginHotspotScoring:
    def test_priority_score_favours_recent(self) -> None:
        now = time.time()
        old = OriginHotspot(
            path="/a.md", source_uri="x",
            fetch_count=5, first_seen=now - 90 * 86400, last_seen=now - 90 * 86400,
        )
        fresh = OriginHotspot(
            path="/b.md", source_uri="x",
            fetch_count=5, first_seen=now, last_seen=now,
        )
        assert fresh.priority_score > old.priority_score

    def test_priority_score_favours_not_found_heavy(self) -> None:
        now = time.time()
        clean = OriginHotspot(path="/a", source_uri="x", fetch_count=10, first_seen=now, last_seen=now)
        missing = OriginHotspot(path="/b", source_uri="x", fetch_count=10, first_seen=now, last_seen=now, not_found_count=10)
        assert missing.priority_score > clean.priority_score


class TestDataclassConstruction:
    def test_evolution_signals_defaults(self) -> None:
        sig = EvolutionSignals()
        assert sig.origin_hotspots == []
        assert sig.citation_weaknesses == []
        assert sig.memory_patterns == []
        assert sig.totals == {}

    def test_citation_weakness_holds_tier_list(self) -> None:
        cw = CitationWeakness(agent_id="A", avg_score=0.5, runs_observed=3, under_cited_tiers=["T2"])
        assert cw.under_cited_tiers == ["T2"]

    def test_memory_pattern_holds_summary(self) -> None:
        mp = MemoryPattern(
            agent_id="A", task_signature="sig",
            summary="summary", recurrence_count=3, avg_success_score=0.9,
        )
        assert mp.summary == "summary"


# ── Preload dispatcher wiring ────────────────────────────────────────────


class TestPreloadIntegration:
    def test_preload_dispatcher_builds_item(self, tmp_path: Path) -> None:
        """_build_preload_item('domain_evolution_signals', root) returns a context_items entry."""
        from harness.core.manifest_loader import _build_preload_item
        _write_origin_log(tmp_path, [
            {"path": "/x.md", "source_uri": "g", "timestamp": time.time()},
            {"path": "/x.md", "source_uri": "g", "timestamp": time.time()},
        ])
        item = _build_preload_item("domain_evolution_signals", tmp_path)
        assert item is not None
        assert item["source"] == "preload:domain_evolution_signals"
        assert "Evolution Signals" in item["content"]
        assert "/x.md" in item["content"]

    def test_preload_dispatcher_without_domain_root_returns_none(self) -> None:
        from harness.core.manifest_loader import _build_preload_item
        assert _build_preload_item("domain_evolution_signals", None) is None

    def test_preload_dispatcher_on_newly_deployed_domain(self, tmp_path: Path) -> None:
        """No audit logs yet — bundle still renders with 'no significant signals' message."""
        from harness.core.manifest_loader import _build_preload_item
        item = _build_preload_item("domain_evolution_signals", tmp_path)
        assert item is not None
        assert "No significant runtime signals" in item["content"]
