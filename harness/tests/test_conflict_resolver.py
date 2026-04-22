"""Tests for ConflictResolverAgent (Gaps 1,2 from the Complexity Upgrade plan).

Covers:
- Manifest/grading bundle shape.
- Position in genesis_build.yaml (between map and discover_tools/engineer_context).
- resolved_knowledge_map + contested_items fields are produced in the
  provider fixture (so downstream agents that consume them see valid data).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.core.expr import evaluate
from harness.tests.genesis_test_provider import GENESIS_OUTPUTS


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_DIR = PROJECT_ROOT / "agents" / "_genesis" / "ConflictResolverAgent" / "v1"
BUILD_WF = PROJECT_ROOT / "workflows" / "genesis" / "genesis_build.yaml"


class TestConflictResolverBundle:
    def test_bundle_files_present(self) -> None:
        assert (AGENT_DIR / "agent_manifest.yaml").exists()
        assert (AGENT_DIR / "system_prompt.md").exists()
        assert (AGENT_DIR / "grading_criteria.yaml").exists()

    def test_output_schema_has_resolved_map(self) -> None:
        manifest = yaml.safe_load((AGENT_DIR / "agent_manifest.yaml").read_text())
        required = manifest["output_schema"]["required"]
        assert "resolved_knowledge_map" in required
        assert "contested_items" in required
        assert "resolution_summary" in required

    def test_grading_threshold_at_least_075(self) -> None:
        data = yaml.safe_load((AGENT_DIR / "grading_criteria.yaml").read_text())
        assert data.get("threshold", 0) >= 0.75

    def test_read_only_permissions(self) -> None:
        """Plan principle: conflict resolver never modifies source repos."""
        manifest = yaml.safe_load((AGENT_DIR / "agent_manifest.yaml").read_text())
        perms = manifest.get("permissions", {})
        assert perms.get("file_edit") == "deny"
        assert perms.get("file_create") == "deny"


class TestConflictResolverPipelinePosition:
    def test_resolve_step_between_map_and_fanout(self) -> None:
        wf = yaml.safe_load(BUILD_WF.read_text())
        steps = {s["name"]: s for s in wf["steps"]}
        assert "resolve" in steps, "genesis_build.yaml must include the 'resolve' step"
        assert "map" in steps["resolve"].get("depends_on", [])
        # discover_tools and engineer_context should fan out *after* resolve
        assert "resolve" in steps["discover_tools"].get("depends_on", [])
        assert "resolve" in steps["engineer_context"].get("depends_on", [])

    def test_resolver_agent_is_wired(self) -> None:
        wf = yaml.safe_load(BUILD_WF.read_text())
        resolve_step = next(s for s in wf["steps"] if s["name"] == "resolve")
        assert "ConflictResolverAgent" in resolve_step["agent"]


class TestConflictResolverFixture:
    def test_provider_emits_expected_shape(self) -> None:
        out = GENESIS_OUTPUTS["ConflictResolverAgent"]
        assert "resolved_knowledge_map" in out
        assert "contested_items" in out
        assert isinstance(out["contested_items"], list)

    def test_resolve_gate_expression_passes_on_fixture(self) -> None:
        """Gate condition must evaluate cleanly against the provider's output."""
        wf = yaml.safe_load(BUILD_WF.read_text())
        resolve_step = next(s for s in wf["steps"] if s["name"] == "resolve")
        gate = resolve_step.get("gate", {})
        cond = gate.get("condition")
        if cond is None:
            pytest.skip("resolve step has no gate; nothing to evaluate")
        ctx = {
            "status": "success",
            "output": GENESIS_OUTPUTS["ConflictResolverAgent"],
        }
        assert evaluate(cond, ctx) is True
