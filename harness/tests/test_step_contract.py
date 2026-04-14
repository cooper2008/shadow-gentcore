"""Tests for StepArtifact and propagate_confidence in step_contract.py."""
from __future__ import annotations

import pytest

from harness.core.step_contract import StepArtifact, propagate_confidence


# ---------------------------------------------------------------------------
# StepArtifact.from_result
# ---------------------------------------------------------------------------


class TestStepArtifactFromResult:
    def test_valid_result(self) -> None:
        result = {
            "status": "completed",
            "output": "some output",
            "agent_id": "agent-42",
        }
        artifact = StepArtifact.from_result("step1", result, agent_id="agent-42")
        assert artifact.step_name == "step1"
        assert artifact.agent_id == "agent-42"
        assert artifact.status == "completed"
        assert artifact.output == "some output"
        assert artifact.confidence == 1.0

    def test_missing_fields_use_defaults(self) -> None:
        artifact = StepArtifact.from_result("step_empty", {})
        assert artifact.step_name == "step_empty"
        assert artifact.agent_id == ""
        assert artifact.status == "completed"
        assert artifact.output is None
        assert artifact.confidence == 1.0
        assert artifact.schema_version == "1.0"

    def test_extracts_confidence_from_validation_score(self) -> None:
        result = {
            "status": "completed",
            "output": "text",
            "_validation": {"passed": True, "score": 0.72, "issues": []},
        }
        artifact = StepArtifact.from_result("step_val", result)
        assert artifact.confidence == pytest.approx(0.72)

    def test_content_field_used_when_output_absent(self) -> None:
        result = {"content": "content value", "status": "completed"}
        artifact = StepArtifact.from_result("step_content", result)
        assert artifact.output == "content value"

    def test_result_field_fallback(self) -> None:
        result = {"result": "result value", "status": "completed"}
        artifact = StepArtifact.from_result("step_result", result)
        assert artifact.output == "result value"

    def test_agent_id_falls_back_to_result_dict(self) -> None:
        result = {"agent_id": "from-dict", "status": "completed"}
        artifact = StepArtifact.from_result("step_aid", result)
        assert artifact.agent_id == "from-dict"

    def test_agent_id_param_overrides_dict(self) -> None:
        result = {"agent_id": "from-dict", "status": "completed"}
        artifact = StepArtifact.from_result("step_aid", result, agent_id="explicit")
        assert artifact.agent_id == "explicit"

    def test_non_numeric_validation_score_defaults_to_1(self) -> None:
        result = {"_validation": {"score": "not-a-number", "passed": False, "issues": []}}
        artifact = StepArtifact.from_result("step_bad_score", result)
        assert artifact.confidence == 1.0

    def test_none_validation_score_defaults_to_1(self) -> None:
        result = {"_validation": {"score": None, "passed": False, "issues": []}}
        artifact = StepArtifact.from_result("step_none_score", result)
        assert artifact.confidence == 1.0

    def test_metadata_excludes_reserved_keys(self) -> None:
        result = {
            "output": "x",
            "content": "y",
            "result": "z",
            "status": "completed",
            "_validation": {},
            "agent_id": "a",
            "extra_key": "keep_me",
        }
        artifact = StepArtifact.from_result("step_meta", result)
        assert "extra_key" in artifact.metadata
        for key in ("output", "content", "result", "status", "_validation", "agent_id"):
            assert key not in artifact.metadata


# ---------------------------------------------------------------------------
# Confidence clamping
# ---------------------------------------------------------------------------


class TestConfidenceClamping:
    def test_confidence_clamped_above_1(self) -> None:
        result = {"_validation": {"score": 1.5, "passed": True, "issues": []}}
        artifact = StepArtifact.from_result("step_clamp_high", result)
        assert artifact.confidence == 1.0

    def test_confidence_clamped_below_0(self) -> None:
        result = {"_validation": {"score": -0.3, "passed": False, "issues": []}}
        artifact = StepArtifact.from_result("step_clamp_low", result)
        assert artifact.confidence == 0.0

    def test_direct_field_validation_ge_le(self) -> None:
        with pytest.raises(Exception):
            StepArtifact(step_name="s", confidence=1.1)
        with pytest.raises(Exception):
            StepArtifact(step_name="s", confidence=-0.1)


# ---------------------------------------------------------------------------
# propagate_confidence
# ---------------------------------------------------------------------------


class TestPropagateConfidence:
    def _art(self, name: str, conf: float) -> StepArtifact:
        return StepArtifact(step_name=name, confidence=conf)

    def test_no_dependencies_returns_own_confidence(self) -> None:
        art = self._art("step", 0.9)
        assert propagate_confidence(art, []) == pytest.approx(0.9)

    def test_own_is_lower_than_deps(self) -> None:
        art = self._art("step", 0.5)
        deps = [self._art("dep1", 0.9)]
        assert propagate_confidence(art, deps) == pytest.approx(0.5)

    def test_dep_is_lower_than_own(self) -> None:
        art = self._art("step", 0.9)
        deps = [self._art("dep1", 0.8), self._art("dep2", 0.95)]
        assert propagate_confidence(art, deps) == pytest.approx(0.8)

    def test_multiple_deps_minimum_wins(self) -> None:
        art = self._art("step", 1.0)
        deps = [self._art("d1", 0.6), self._art("d2", 0.85), self._art("d3", 0.4)]
        assert propagate_confidence(art, deps) == pytest.approx(0.4)

    def test_all_perfect_confidence(self) -> None:
        art = self._art("step", 1.0)
        deps = [self._art("d1", 1.0), self._art("d2", 1.0)]
        assert propagate_confidence(art, deps) == pytest.approx(1.0)

    def test_zero_dep_propagates(self) -> None:
        art = self._art("step", 0.9)
        deps = [self._art("d1", 0.0)]
        assert propagate_confidence(art, deps) == pytest.approx(0.0)
