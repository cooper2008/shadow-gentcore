"""Tests for ContextVerifierAgent (Gap 4 — re-read verification).

Confirms:
- Bundle present with file_read tool.
- verify step exists after engineer_context.
- verify_gate blocks on grounding_score < 0.7.
- verify_to_context feedback loop is wired.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from harness.core.expr import evaluate
from harness.tests.genesis_test_provider import GENESIS_OUTPUTS


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_DIR = PROJECT_ROOT / "agents" / "_genesis" / "ContextVerifierAgent" / "v1"
BUILD_WF = PROJECT_ROOT / "workflows" / "genesis" / "genesis_build.yaml"


class TestContextVerifierBundle:
    def test_bundle_files_present(self) -> None:
        assert (AGENT_DIR / "agent_manifest.yaml").exists()
        assert (AGENT_DIR / "system_prompt.md").exists()
        assert (AGENT_DIR / "grading_criteria.yaml").exists()

    def test_has_file_read_tool(self) -> None:
        """Plan requirement: ContextVerifier re-reads cited sources."""
        manifest = yaml.safe_load((AGENT_DIR / "agent_manifest.yaml").read_text())
        tool_names = {t["name"] for t in manifest.get("tools", [])}
        assert "file_read" in tool_names

    def test_output_schema_has_grounding(self) -> None:
        manifest = yaml.safe_load((AGENT_DIR / "agent_manifest.yaml").read_text())
        required = manifest["output_schema"]["required"]
        assert "grounding_score" in required
        assert "unsupported_claims" in required

    def test_grading_threshold_at_least_075(self) -> None:
        data = yaml.safe_load((AGENT_DIR / "grading_criteria.yaml").read_text())
        assert data.get("threshold", 0) >= 0.75


class TestVerifyStepAndGate:
    def test_verify_step_after_engineer_context(self) -> None:
        wf = yaml.safe_load(BUILD_WF.read_text())
        steps = {s["name"]: s for s in wf["steps"]}
        assert "verify" in steps
        assert "engineer_context" in steps["verify"].get("depends_on", [])

    def test_architect_depends_on_verify(self) -> None:
        """verify must gate architect, not just run alongside."""
        wf = yaml.safe_load(BUILD_WF.read_text())
        arch = next(s for s in wf["steps"] if s["name"] == "architect")
        assert "verify" in arch.get("depends_on", [])

    def test_verify_gate_threshold_is_07(self) -> None:
        wf = yaml.safe_load(BUILD_WF.read_text())
        verify = next(s for s in wf["steps"] if s["name"] == "verify")
        cond = verify["gate"]["condition"]
        assert "grounding_score" in cond
        assert "0.7" in cond

    def test_verify_gate_passes_on_high_grounding(self) -> None:
        ctx = {"status": "success", "output": {"grounding_score": 0.9}}
        wf = yaml.safe_load(BUILD_WF.read_text())
        cond = next(s for s in wf["steps"] if s["name"] == "verify")["gate"]["condition"]
        assert evaluate(cond, ctx) is True

    def test_verify_gate_blocks_on_low_grounding(self) -> None:
        ctx = {"status": "success", "output": {"grounding_score": 0.5}}
        wf = yaml.safe_load(BUILD_WF.read_text())
        cond = next(s for s in wf["steps"] if s["name"] == "verify")["gate"]["condition"]
        assert evaluate(cond, ctx) is False


class TestVerifyFeedbackLoop:
    def test_verify_to_context_loop_wired(self) -> None:
        wf = yaml.safe_load(BUILD_WF.read_text())
        loops = {fl["name"]: fl for fl in wf.get("feedback_loops", [])}
        assert "verify_to_context" in loops
        loop = loops["verify_to_context"]
        assert loop["from_step"] == "verify"
        assert loop["to_step"] == "engineer_context"
        assert loop.get("max_iterations", 0) >= 1


class TestVerifierFixture:
    def test_provider_emits_high_grounding_by_default(self) -> None:
        out = GENESIS_OUTPUTS["ContextVerifierAgent"]
        assert out["grounding_score"] >= 0.7
        assert isinstance(out["unsupported_claims"], list)
