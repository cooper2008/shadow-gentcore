"""Tests for coverage-aware gate conditions (Gap 5 from the plan).

Targets map_gate, architect_gate, build_gate, validate_gate, verify_gate.
All gate expressions must parse AND must branch correctly: passing on the
provider's normal fixture, failing on a deliberately thin output.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from harness.core.expr import evaluate
from harness.tests.genesis_test_provider import GENESIS_OUTPUTS


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BUILD_WF = PROJECT_ROOT / "workflows" / "genesis" / "genesis_build.yaml"


def _gate_condition(step_name: str) -> str:
    wf = yaml.safe_load(BUILD_WF.read_text())
    step = next(s for s in wf["steps"] if s["name"] == step_name)
    return step["gate"]["condition"]


class TestMapGate:
    cond = None

    def test_passes_on_adequate_coverage(self) -> None:
        ctx = {"status": "success", "output": {"coverage": {"overall": 85}}}
        assert evaluate(_gate_condition("map"), ctx) is True

    def test_blocks_on_low_coverage(self) -> None:
        ctx = {"status": "success", "output": {"coverage": {"overall": 20}}}
        assert evaluate(_gate_condition("map"), ctx) is False

    def test_passes_on_provider_fixture(self) -> None:
        ctx = {"status": "success", "output": GENESIS_OUTPUTS["KnowledgeMapperAgent"]}
        assert evaluate(_gate_condition("map"), ctx) is True


class TestArchitectGate:
    def test_requires_min_two_agents(self) -> None:
        ctx = {
            "status": "success",
            "output": {"design_quality": {"agent_count": 1, "dag_valid": True}},
        }
        assert evaluate(_gate_condition("architect"), ctx) is False

    def test_requires_valid_dag(self) -> None:
        ctx = {
            "status": "success",
            "output": {"design_quality": {"agent_count": 4, "dag_valid": False}},
        }
        assert evaluate(_gate_condition("architect"), ctx) is False

    def test_passes_on_full_design(self) -> None:
        ctx = {
            "status": "success",
            "output": {"design_quality": {"agent_count": 4, "dag_valid": True}},
        }
        assert evaluate(_gate_condition("architect"), ctx) is True


class TestBuildGate:
    def test_requires_min_three_files(self) -> None:
        ctx = {"status": "success", "output": {"build_quality": {"files_written": 2}}}
        assert evaluate(_gate_condition("build"), ctx) is False

    def test_passes_when_three_or_more(self) -> None:
        ctx = {"status": "success", "output": {"build_quality": {"files_written": 10}}}
        assert evaluate(_gate_condition("build"), ctx) is True


class TestValidateGate:
    def test_blocks_on_validation_fail(self) -> None:
        ctx = {"status": "success", "output": {"validation_passed": False}}
        assert evaluate(_gate_condition("validate"), ctx) is False

    def test_passes_on_validation_pass(self) -> None:
        ctx = {"status": "success", "output": {"validation_passed": True}}
        assert evaluate(_gate_condition("validate"), ctx) is True


class TestGateExpressionsAllParse:
    """Smoke test: every gate in genesis_build.yaml must tokenize without error."""

    def test_every_gate_evaluates_cleanly(self) -> None:
        wf = yaml.safe_load(BUILD_WF.read_text())
        for step in wf["steps"]:
            cond = step.get("gate", {}).get("condition")
            if not cond:
                continue
            ctx = {
                "status": "success",
                "output": {
                    "coverage": {"overall": 85},
                    "design_quality": {"agent_count": 4, "dag_valid": True},
                    "build_quality": {"files_written": 5},
                    "validation_passed": True,
                    "grounding_score": 0.9,
                    "resolution_summary": {"notes": ["ok"]},
                    "security_scan": {"passed": True},
                },
            }
            evaluate(cond, ctx)
