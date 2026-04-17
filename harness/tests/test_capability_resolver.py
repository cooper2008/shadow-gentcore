"""Tests for CapabilityResolver (B2)."""

from __future__ import annotations

from pathlib import Path

import yaml

from harness.core.capability_resolver import CapabilityResolver


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")


class TestDefaultConfig:
    """Resolver against the shipped config/capabilities.yaml."""

    def test_known_capability_resolves_packs(self) -> None:
        resolver = CapabilityResolver()
        packs = resolver.resolve_packs("cloud_query")
        assert "toolpack://cloud/aws" in packs
        assert "toolpack://cloud/aws_advanced" in packs
        assert "toolpack://cloud/kubectl" in packs

    def test_unknown_capability_returns_empty(self) -> None:
        resolver = CapabilityResolver()
        assert resolver.resolve_packs("nonexistent_capability") == []

    def test_stage_defaults_for_known_stage(self) -> None:
        resolver = CapabilityResolver()
        caps = resolver.resolve_capabilities_for_stage("CodeWriter")
        assert "code_read" in caps
        assert "code_write" in caps

    def test_unknown_stage_returns_empty(self) -> None:
        resolver = CapabilityResolver()
        assert resolver.resolve_capabilities_for_stage("MysteryStage") == []
        assert resolver.resolve_packs_for_stage("MysteryStage") == []

    def test_resolve_packs_for_stage_flattens_and_dedupes(self) -> None:
        resolver = CapabilityResolver()
        packs = resolver.resolve_packs_for_stage("Investigate")
        # Investigate -> [cloud_query, observability, log_analysis, knowledge_retrieval]
        # observability + log_analysis both reference cloudwatch + datadog → dedup expected
        assert "toolpack://cloud/aws" in packs
        assert "toolpack://observability/cloudwatch" in packs
        assert "toolpack://core/runbook_retrieval" in packs
        # No duplicates
        assert len(packs) == len(set(packs))

    def test_known_capabilities_includes_b1_b2_set(self) -> None:
        """Sanity: the shipped map declares the audit's expected capability set."""
        resolver = CapabilityResolver()
        capabilities = set(resolver.known_capabilities())
        for required in (
            "cloud_query",
            "cloud_control",
            "observability",
            "log_analysis",
            "alerting",
            "knowledge_retrieval",
            "runbook_exec",
            "ticketing",
            "code_read",
            "code_write",
        ):
            assert required in capabilities, f"missing capability: {required}"

    def test_known_stages_includes_b1_generics(self) -> None:
        """Sanity: stage_defaults declares the 6 new B1 generic stages."""
        resolver = CapabilityResolver()
        stages = set(resolver.known_stages())
        for required in ("Triage", "Investigate", "Execute", "Respond", "Summarize", "Retrieve"):
            assert required in stages, f"missing B1 stage: {required}"

    def test_known_stages_includes_existing_shared_stages(self) -> None:
        """Sanity: stage_defaults covers the existing 20 _shared/ stages."""
        resolver = CapabilityResolver()
        stages = set(resolver.known_stages())
        for required in ("CodeWriter", "TestRunner", "Reviewer", "Deploy", "Notifier"):
            assert required in stages, f"missing existing stage: {required}"


class TestMissingFile:
    """Empty-on-missing fallback per audit requirement."""

    def test_returns_empty_when_config_absent(self, tmp_path: Path) -> None:
        resolver = CapabilityResolver(config_dir=tmp_path)
        assert resolver.resolve_packs("anything") == []
        assert resolver.resolve_capabilities_for_stage("anything") == []
        assert resolver.resolve_packs_for_stage("anything") == []
        assert resolver.known_capabilities() == []
        assert resolver.known_stages() == []


class TestExplicitConfig:
    """Custom config dir for testability + capability map evolution."""

    def test_loads_from_explicit_dir(self, tmp_path: Path) -> None:
        registry = {
            "capabilities": {
                "alpha_query": {
                    "packs": ["alpha/one", "alpha/two"],
                    "description": "Test capability",
                },
            },
            "stage_defaults": {
                "AlphaStage": ["alpha_query"],
            },
        }
        _write_yaml(tmp_path / "capabilities.yaml", registry)
        resolver = CapabilityResolver(config_dir=tmp_path)
        assert resolver.resolve_packs("alpha_query") == [
            "toolpack://alpha/one",
            "toolpack://alpha/two",
        ]
        assert resolver.resolve_capabilities_for_stage("AlphaStage") == ["alpha_query"]
        assert resolver.resolve_packs_for_stage("AlphaStage") == [
            "toolpack://alpha/one",
            "toolpack://alpha/two",
        ]

    def test_malformed_yaml_falls_back_silently(self, tmp_path: Path) -> None:
        (tmp_path / "capabilities.yaml").write_text(": :\n: not valid yaml ::", encoding="utf-8")
        resolver = CapabilityResolver(config_dir=tmp_path)
        # Logs a warning but does not raise
        assert resolver.resolve_packs("anything") == []

    def test_reload_picks_up_changes(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "capabilities.yaml", {
            "capabilities": {"v1_cap": {"packs": ["v1/pack"]}},
            "stage_defaults": {},
        })
        resolver = CapabilityResolver(config_dir=tmp_path)
        assert resolver.resolve_packs("v1_cap") == ["toolpack://v1/pack"]

        _write_yaml(tmp_path / "capabilities.yaml", {
            "capabilities": {"v2_cap": {"packs": ["v2/pack"]}},
            "stage_defaults": {},
        })
        # Without reload — still cached
        assert resolver.resolve_packs("v1_cap") == ["toolpack://v1/pack"]
        assert resolver.resolve_packs("v2_cap") == []

        resolver.reload()
        assert resolver.resolve_packs("v1_cap") == []
        assert resolver.resolve_packs("v2_cap") == ["toolpack://v2/pack"]

    def test_capability_with_non_string_pack_entry_is_skipped(self, tmp_path: Path) -> None:
        """Defensive: bad data shape doesn't crash the resolver."""
        registry = {
            "capabilities": {
                "broken": {"packs": ["good/pack", 42, None, "another/good"]},
            },
            "stage_defaults": {},
        }
        _write_yaml(tmp_path / "capabilities.yaml", registry)
        resolver = CapabilityResolver(config_dir=tmp_path)
        assert resolver.resolve_packs("broken") == [
            "toolpack://good/pack",
            "toolpack://another/good",
        ]
