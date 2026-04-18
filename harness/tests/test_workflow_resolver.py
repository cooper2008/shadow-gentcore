"""Tests for harness.core.workflow_resolver — the override + discovery merger."""

from __future__ import annotations

from harness.core.workflow_resolver import (
    CONFIDENCE_THRESHOLD,
    MIN_PROCESSES,
    STACK_DEFAULTS,
    normalize_architect_output,
    resolve_workflow_processes,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


DISCOVERED_SAMPLE = [
    {"name": "feature_delivery", "confidence": 0.92, "signals": ["git log", "PR template"]},
    {"name": "bug_fix",          "confidence": 0.88, "signals": ["git log", "issue template"]},
    {"name": "refactor",         "confidence": 0.71, "signals": ["git log"]},
    {"name": "dep_upgrade",      "confidence": 0.64, "signals": ["Dependabot"]},
    {"name": "docs_refresh",     "confidence": 0.55, "signals": ["CONTRIBUTING.md"]},
    {"name": "weak_signal",      "confidence": 0.22, "signals": ["one commit"]},  # below threshold
]


# ── 1. Full override short-circuits discovery ─────────────────────────────


class TestFullOverride:
    def test_explicit_processes_win_over_discovery(self) -> None:
        report = resolve_workflow_processes(
            domain_workflows={"processes": ["alpha", "beta", "gamma"]},
            discovered=DISCOVERED_SAMPLE,
        )
        assert report.process_names == ["alpha", "beta", "gamma"]
        assert all(p.source == "override" for p in report.processes)
        assert all(p.confidence == "override" for p in report.processes)
        assert "full override" in report.source_summary

    def test_full_override_ignores_stack_defaults(self) -> None:
        """Even if the override lists only 1 process, stack defaults don't fill in."""
        report = resolve_workflow_processes(
            domain_workflows={"processes": ["just_one"]},
            discovered=DISCOVERED_SAMPLE,
            stack_key="backend-api",
        )
        assert report.process_names == ["just_one"]

    def test_triage_override_preserved(self) -> None:
        triage = {
            "classifier": "_shared/TriageAgent/v1",
            "buckets":  ["foo", "bar"],
            "routes":   {"foo": "alpha", "bar": "beta", "unknown": "human_review"},
        }
        report = resolve_workflow_processes(
            domain_workflows={"processes": ["alpha", "beta"], "triage": triage},
            discovered=None,
        )
        assert report.triage_override == triage


# ── 2. Auto-discovery path ────────────────────────────────────────────────


class TestAutoDiscovery:
    def test_filters_below_confidence_threshold(self) -> None:
        report = resolve_workflow_processes(
            domain_workflows=None,
            discovered=DISCOVERED_SAMPLE,
        )
        names = report.process_names
        assert "weak_signal" not in names  # 0.22 < 0.5 filtered out
        assert len(names) == 5

    def test_confidence_threshold_constant_matches_spec(self) -> None:
        # This test pins the contract — changing CONFIDENCE_THRESHOLD is a
        # behaviour change that should be documented.
        assert CONFIDENCE_THRESHOLD == 0.5

    def test_preserves_order_of_discovered(self) -> None:
        report = resolve_workflow_processes(
            domain_workflows=None,
            discovered=DISCOVERED_SAMPLE,
        )
        assert report.process_names == [
            "feature_delivery", "bug_fix", "refactor", "dep_upgrade", "docs_refresh"
        ]

    def test_auto_sources_tagged(self) -> None:
        report = resolve_workflow_processes(
            domain_workflows=None,
            discovered=DISCOVERED_SAMPLE,
        )
        assert all(p.source == "auto" for p in report.processes)


# ── 3. Delta overrides ────────────────────────────────────────────────────


class TestDeltaOverride:
    def test_add_processes_appended(self) -> None:
        report = resolve_workflow_processes(
            domain_workflows={"add_processes": ["custom_data_export"]},
            discovered=DISCOVERED_SAMPLE,
        )
        assert "custom_data_export" in report.process_names
        added = next(p for p in report.processes if p.name == "custom_data_export")
        assert added.source == "delta"
        assert added.confidence == "override"

    def test_exclude_processes_removes_auto(self) -> None:
        report = resolve_workflow_processes(
            domain_workflows={"exclude_processes": ["refactor"]},
            discovered=DISCOVERED_SAMPLE,
        )
        assert "refactor" not in report.process_names
        assert "feature_delivery" in report.process_names

    def test_add_and_exclude_together(self) -> None:
        report = resolve_workflow_processes(
            domain_workflows={
                "add_processes": ["gdpr_erase"],
                "exclude_processes": ["refactor", "dep_upgrade"],
            },
            discovered=DISCOVERED_SAMPLE,
        )
        names = report.process_names
        assert "gdpr_erase" in names
        assert "refactor" not in names
        assert "dep_upgrade" not in names
        assert "feature_delivery" in names  # auto kept

    def test_add_dedups_against_auto(self) -> None:
        """Adding a process that's already in auto-discovery doesn't duplicate."""
        report = resolve_workflow_processes(
            domain_workflows={"add_processes": ["feature_delivery"]},
            discovered=DISCOVERED_SAMPLE,
        )
        names = report.process_names
        assert names.count("feature_delivery") == 1


# ── 4. Greenfield / low-signal fallback ───────────────────────────────────


class TestStackDefaults:
    def test_empty_discovery_falls_back_to_generic(self) -> None:
        report = resolve_workflow_processes(
            domain_workflows=None,
            discovered=[],
        )
        assert len(report.processes) >= MIN_PROCESSES
        assert all(p.source == "default" for p in report.processes)
        assert report.process_names == STACK_DEFAULTS["generic"]

    def test_stack_key_selects_right_defaults(self) -> None:
        report = resolve_workflow_processes(
            domain_workflows=None,
            discovered=[],
            stack_key="aws-ops",
        )
        assert report.process_names == STACK_DEFAULTS["aws-ops"]
        assert "incident_triage" in report.process_names

    def test_unknown_stack_key_falls_back_to_generic(self) -> None:
        report = resolve_workflow_processes(
            domain_workflows=None,
            discovered=[],
            stack_key="unknown-stack-xyz",
        )
        assert report.process_names == STACK_DEFAULTS["generic"]

    def test_below_threshold_triggers_defaults(self) -> None:
        """Two auto-discovered entries -> stack defaults fill in."""
        partial = [
            {"name": "feature_delivery", "confidence": 0.9, "signals": ["git log"]},
            {"name": "bug_fix",          "confidence": 0.8, "signals": ["git log"]},
        ]
        report = resolve_workflow_processes(
            domain_workflows=None,
            discovered=partial,
            stack_key="backend-api",
        )
        # Auto entries stay, defaults fill in
        assert "feature_delivery" in report.process_names
        assert "bug_fix" in report.process_names
        assert len(report.process_names) >= MIN_PROCESSES

    def test_excludes_respected_in_fallback(self) -> None:
        """exclude_processes applies to stack defaults too."""
        report = resolve_workflow_processes(
            domain_workflows={"exclude_processes": ["refactor"]},
            discovered=[],
            stack_key="backend-api",
        )
        assert "refactor" not in report.process_names


# ── 5. CLI presentation ───────────────────────────────────────────────────


class TestArchitectOutputNormalization:
    def test_singular_workflow_design_lifted_to_plural(self) -> None:
        single = {"name": "feature_delivery", "steps": [], "gates": []}
        out = normalize_architect_output({"workflow_design": single})
        assert out["workflow_designs"] == [single]

    def test_plural_passthrough_when_already_present(self) -> None:
        designs = [
            {"name": "a", "steps": [], "gates": []},
            {"name": "b", "steps": [], "gates": []},
        ]
        out = normalize_architect_output({"workflow_designs": designs})
        assert out["workflow_designs"] is designs  # no copy, no reshape

    def test_plural_wins_over_legacy_singular_when_both_present(self) -> None:
        """Defensive: if a transitional output has both, plural is authoritative."""
        out = normalize_architect_output({
            "workflow_design":  {"name": "legacy", "steps": [], "gates": []},
            "workflow_designs": [{"name": "new",   "steps": [], "gates": []}],
        })
        assert [d["name"] for d in out["workflow_designs"]] == ["new"]

    def test_returns_copy_not_mutation(self) -> None:
        original = {"workflow_design": {"name": "x", "steps": [], "gates": []}}
        _ = normalize_architect_output(original)
        assert "workflow_designs" not in original  # caller's dict untouched


class TestFormatCli:
    def test_format_cli_includes_each_process(self) -> None:
        report = resolve_workflow_processes(
            domain_workflows=None,
            discovered=DISCOVERED_SAMPLE,
        )
        rendered = report.format_cli()
        for name in report.process_names:
            assert name in rendered
        assert "Override via" in rendered

    def test_format_cli_reports_override_source(self) -> None:
        report = resolve_workflow_processes(
            domain_workflows={"processes": ["a", "b"]},
            discovered=None,
        )
        rendered = report.format_cli()
        assert "full override" in rendered

    def test_format_cli_marks_triage_override(self) -> None:
        triage = {"classifier": "_shared/TriageAgent/v1", "buckets": [], "routes": {}}
        report = resolve_workflow_processes(
            domain_workflows={"processes": ["a"], "triage": triage},
            discovered=None,
        )
        assert "user-declared" in report.format_cli()
