"""Tests for B5 — catalog-driven AgentArchitectAgent/v2 + feature flag swap."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.core.manifest_loader import ManifestLoader


V1_PATH = Path(__file__).resolve().parent.parent.parent / "agents/_genesis/AgentArchitectAgent/v1"
V2_PATH = Path(__file__).resolve().parent.parent.parent / "agents/_genesis/AgentArchitectAgent/v2"


class TestV2Layout:
    """v2 directory ships with the expected file set."""

    def test_v2_files_exist(self) -> None:
        assert (V2_PATH / "agent_manifest.yaml").exists()
        assert (V2_PATH / "system_prompt.md").exists()
        assert (V2_PATH / "grading_criteria.yaml").exists()

    def test_v2_manifest_id_matches_dir(self) -> None:
        manifest = yaml.safe_load((V2_PATH / "agent_manifest.yaml").read_text())
        assert manifest["id"] == "_genesis/AgentArchitectAgent/v2"
        assert manifest["version"] == "2.0.0"


class TestAuditRequiredSchemaChanges:
    """Schema fixes from audit §2 G-ARC + §5 B5."""

    @pytest.fixture(scope="class")
    def manifest(self) -> dict:
        return yaml.safe_load((V2_PATH / "agent_manifest.yaml").read_text())

    def test_harness_is_required_per_roster_entry(self, manifest: dict) -> None:
        """G-ARC fix: pre-v2 validator missed harness; v2 enforces it."""
        roster_item = manifest["output_schema"]["properties"]["agent_roster"]["items"]
        assert "harness" in roster_item["required"]

    def test_capability_bindings_is_a_property(self, manifest: dict) -> None:
        """B5 — per-step capability resolution traceable in output."""
        roster_item = manifest["output_schema"]["properties"]["agent_roster"]["items"]
        assert "capability_bindings" in roster_item["properties"]
        binding = roster_item["properties"]["capability_bindings"]["items"]
        assert "capability" in binding["required"]
        assert "resolved_packs" in binding["required"]

    def test_decision_field_enum(self, manifest: dict) -> None:
        """B5 — explicit reuse/synthesize decision on every roster entry."""
        roster_item = manifest["output_schema"]["properties"]["agent_roster"]["items"]
        decision = roster_item["properties"]["decision"]
        assert "decision" in roster_item["required"]
        assert set(decision["enum"]) == {
            "reuse-core",
            "reuse-with-prompt-override",
            "synthesize-new",
            "ask-human",
        }

    def test_category_enum_includes_ops_and_compliance(self, manifest: dict) -> None:
        """S3-aware: v2 knows about the new categories so retagged stages compose."""
        roster_item = manifest["output_schema"]["properties"]["agent_roster"]["items"]
        categories = set(roster_item["properties"]["category"]["enum"])
        assert "ops" in categories
        assert "compliance" in categories
        # Legacy categories still present
        assert "reasoning" in categories
        assert "fast-codegen" in categories

    def test_reuse_ratio_in_design_quality(self, manifest: dict) -> None:
        """B5 — architecture health metric: how much did v2 reuse?"""
        dq = manifest["output_schema"]["properties"]["design_quality"]
        assert "reuse_ratio" in dq["required"]
        assert dq["properties"]["reuse_ratio"]["minimum"] == 0
        assert dq["properties"]["reuse_ratio"]["maximum"] == 1

    def test_industry_input_still_declared(self, manifest: dict) -> None:
        """B4 — industry field remains an input (v1 had it dead; v2 uses it)."""
        assert "industry" in manifest["input_schema"]["properties"]

    def test_stage_catalog_input_declared(self, manifest: dict) -> None:
        """B5 — catalog is now a first-class input, not re-scanned per run."""
        assert "stage_catalog" in manifest["input_schema"]["properties"]

    def test_capability_map_input_declared(self, manifest: dict) -> None:
        assert "capability_map" in manifest["input_schema"]["properties"]


class TestSystemPromptCatalogDriven:
    """v2 prompt emphasises composition from catalog over roster invention."""

    @pytest.fixture(scope="class")
    def prompt(self) -> str:
        return (V2_PATH / "system_prompt.md").read_text()

    def test_prompt_emphasises_reuse(self, prompt: str) -> None:
        assert "reuse" in prompt.lower()
        assert "catalog" in prompt.lower()
        assert "composition" in prompt.lower()

    def test_prompt_documents_decision_values(self, prompt: str) -> None:
        for value in ("reuse-core", "reuse-with-prompt-override", "synthesize-new", "ask-human"):
            assert value in prompt

    def test_prompt_references_capability_bindings(self, prompt: str) -> None:
        assert "capability_bindings" in prompt

    def test_prompt_references_reuse_ratio(self, prompt: str) -> None:
        assert "reuse_ratio" in prompt or "reuse ratio" in prompt.lower()


class TestFeatureFlagSwap:
    """GENTCORE_ARCHITECT_V2=1 rewrites v1 → v2 in load_workflow."""

    def _make_workflow(self, tmp_path: Path) -> Path:
        wf = {
            "name": "genesis_build",
            "domain": "_genesis",
            "steps": [
                {"name": "scan", "agent": "_genesis/SourceScannerAgent/v1"},
                {"name": "architect", "agent": "_genesis/AgentArchitectAgent/v1"},
                {"name": "build", "agent": "_genesis/AgentBuilderAgent/v1"},
            ],
        }
        path = tmp_path / "test_workflow.yaml"
        path.write_text(yaml.dump(wf), encoding="utf-8")
        return path

    def test_flag_unset_defaults_to_v2(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """v2 is now the default — unset flag loads the catalog-driven v2 agent."""
        monkeypatch.delenv("GENTCORE_ARCHITECT_V2", raising=False)
        loader = ManifestLoader()
        wf = loader.load_workflow(self._make_workflow(tmp_path))
        architect_step = next(s for s in wf["steps"] if s["name"] == "architect")
        assert architect_step["agent"] == "_genesis/AgentArchitectAgent/v2"

    def test_flag_on_swaps_to_v2(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GENTCORE_ARCHITECT_V2", "1")
        loader = ManifestLoader()
        wf = loader.load_workflow(self._make_workflow(tmp_path))
        architect_step = next(s for s in wf["steps"] if s["name"] == "architect")
        assert architect_step["agent"] == "_genesis/AgentArchitectAgent/v2"
        # Other steps untouched
        scan = next(s for s in wf["steps"] if s["name"] == "scan")
        assert scan["agent"] == "_genesis/SourceScannerAgent/v1"

    def test_flag_on_case_insensitive_truthy(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        for truthy in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("GENTCORE_ARCHITECT_V2", truthy)
            loader = ManifestLoader()
            wf = loader.load_workflow(self._make_workflow(tmp_path))
            step = next(s for s in wf["steps"] if s["name"] == "architect")
            assert step["agent"] == "_genesis/AgentArchitectAgent/v2", f"failed for {truthy!r}"

    def test_flag_false_values_force_v1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit falsy values are the opt-out path back to v1."""
        for falsy in ("0", "false", "no", "off"):
            monkeypatch.setenv("GENTCORE_ARCHITECT_V2", falsy)
            loader = ManifestLoader()
            wf = loader.load_workflow(self._make_workflow(tmp_path))
            step = next(s for s in wf["steps"] if s["name"] == "architect")
            assert step["agent"] == "_genesis/AgentArchitectAgent/v1", f"failed for {falsy!r}"

    def test_flag_empty_string_stays_default_v2(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty string is treated as unset → v2 default."""
        monkeypatch.setenv("GENTCORE_ARCHITECT_V2", "")
        loader = ManifestLoader()
        wf = loader.load_workflow(self._make_workflow(tmp_path))
        step = next(s for s in wf["steps"] if s["name"] == "architect")
        assert step["agent"] == "_genesis/AgentArchitectAgent/v2"

    def test_no_architect_step_no_rewrite(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Workflows without the architect step are untouched even with flag on."""
        monkeypatch.setenv("GENTCORE_ARCHITECT_V2", "1")
        wf_data = {
            "name": "minimal",
            "domain": "test",
            "steps": [{"name": "a", "agent": "test/SomeAgent/v1"}],
        }
        path = tmp_path / "minimal.yaml"
        path.write_text(yaml.dump(wf_data), encoding="utf-8")
        loader = ManifestLoader()
        wf = loader.load_workflow(path)
        assert wf["steps"][0]["agent"] == "test/SomeAgent/v1"

    def test_v1_preserved_on_disk(self) -> None:
        """v2 ships alongside v1 — v1 is not renamed or deleted."""
        assert V1_PATH.exists()
        assert (V1_PATH / "agent_manifest.yaml").exists()
