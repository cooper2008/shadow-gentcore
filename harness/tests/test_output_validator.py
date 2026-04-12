"""Tests for OutputValidator — schema validation + grading criteria scoring."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from harness.core.output_validator import OutputValidator


@pytest.fixture
def validator() -> OutputValidator:
    return OutputValidator()


class TestSchemaValidation:
    @pytest.mark.asyncio
    async def test_passes_when_no_schema(self, validator: OutputValidator) -> None:
        result = await validator.validate({"anything": "goes"}, {})
        assert result["passed"]
        assert result["schema_valid"]

    @pytest.mark.asyncio
    async def test_passes_with_valid_output(self, validator: OutputValidator) -> None:
        manifest = {
            "output_schema": {
                "type": "object",
                "required": ["summary", "files_modified"],
                "properties": {
                    "summary": {"type": "string"},
                    "files_modified": {"type": "array"},
                },
            }
        }
        output = {"summary": "Done", "files_modified": ["a.py", "b.py"]}
        result = await validator.validate(output, manifest)
        assert result["schema_valid"]
        assert not result["issues"]

    @pytest.mark.asyncio
    async def test_fails_missing_required_field(self, validator: OutputValidator) -> None:
        manifest = {
            "output_schema": {
                "type": "object",
                "required": ["summary", "score"],
                "properties": {
                    "summary": {"type": "string"},
                    "score": {"type": "number"},
                },
            }
        }
        output = {"summary": "Done"}  # missing "score"
        result = await validator.validate(output, manifest)
        assert not result["schema_valid"]
        assert any("score" in issue for issue in result["issues"])

    @pytest.mark.asyncio
    async def test_fails_wrong_type(self, validator: OutputValidator) -> None:
        manifest = {
            "output_schema": {
                "type": "object",
                "required": ["count"],
                "properties": {"count": {"type": "integer"}},
            }
        }
        output = {"count": "not_a_number"}
        result = await validator.validate(output, manifest)
        assert not result["schema_valid"]
        assert any("count" in issue for issue in result["issues"])


class TestGradingCriteria:
    @pytest.mark.asyncio
    async def test_automated_check_passes(self, validator: OutputValidator, tmp_path: Path) -> None:
        criteria = {
            "criteria": [
                {"name": "has_output", "type": "automated", "check": "exit_code == 0", "weight": 1.0},
            ],
            "threshold": 0.5,
        }
        criteria_path = tmp_path / "grading_criteria.yaml"
        criteria_path.write_text(yaml.dump(criteria))

        manifest = {"grading_criteria_ref": "grading_criteria.yaml"}
        output = {"exit_code": 0}
        result = await validator.validate(output, manifest, agent_dir=str(tmp_path))

        assert result["passed"]
        assert result["score"] >= 0.5
        assert len(result["criteria_results"]) == 1
        assert result["criteria_results"][0]["passed"]

    @pytest.mark.asyncio
    async def test_automated_check_fails(self, validator: OutputValidator, tmp_path: Path) -> None:
        criteria = {
            "criteria": [
                {"name": "tests_pass", "type": "automated", "check": "exit_code == 0", "weight": 1.0},
            ],
            "threshold": 0.75,
        }
        criteria_path = tmp_path / "grading_criteria.yaml"
        criteria_path.write_text(yaml.dump(criteria))

        manifest = {"grading_criteria_ref": "grading_criteria.yaml"}
        output = {"exit_code": 1}  # test failure
        result = await validator.validate(output, manifest, agent_dir=str(tmp_path))

        assert not result["passed"]
        assert result["score"] < 0.75

    @pytest.mark.asyncio
    async def test_multiple_criteria_weighted(self, validator: OutputValidator, tmp_path: Path) -> None:
        criteria = {
            "criteria": [
                {"name": "c1", "type": "automated", "check": "exit_code == 0", "weight": 0.6},
                {"name": "c2", "type": "automated", "check": "coverage >= 80", "weight": 0.4},
            ],
            "threshold": 0.5,
        }
        criteria_path = tmp_path / "grading_criteria.yaml"
        criteria_path.write_text(yaml.dump(criteria))

        manifest = {"grading_criteria_ref": "grading_criteria.yaml"}
        output = {"exit_code": 0, "coverage": 90}  # both pass
        result = await validator.validate(output, manifest, agent_dir=str(tmp_path))

        assert result["passed"]
        assert result["score"] == 1.0

    @pytest.mark.asyncio
    async def test_partial_pass(self, validator: OutputValidator, tmp_path: Path) -> None:
        criteria = {
            "criteria": [
                {"name": "c1", "type": "automated", "check": "exit_code == 0", "weight": 0.5},
                {"name": "c2", "type": "automated", "check": "coverage >= 80", "weight": 0.5},
            ],
            "threshold": 0.4,  # low threshold
        }
        criteria_path = tmp_path / "grading_criteria.yaml"
        criteria_path.write_text(yaml.dump(criteria))

        manifest = {"grading_criteria_ref": "grading_criteria.yaml"}
        output = {"exit_code": 0, "coverage": 50}  # c1 passes, c2 fails
        result = await validator.validate(output, manifest, agent_dir=str(tmp_path))

        assert result["passed"]  # 0.5 score > 0.4 threshold
        assert result["score"] == 0.5


class TestLLMJudge:
    @pytest.mark.asyncio
    async def test_skips_without_provider(self, validator: OutputValidator, tmp_path: Path) -> None:
        criteria = {
            "criteria": [
                {"name": "quality", "type": "llm_judge", "prompt": "Is this good?", "weight": 1.0},
            ],
            "threshold": 0.5,
        }
        (tmp_path / "grading.yaml").write_text(yaml.dump(criteria))

        manifest = {"grading_criteria_ref": "grading.yaml"}
        result = await validator.validate({"content": "hello"}, manifest, agent_dir=str(tmp_path))

        # Should skip (not fail) without provider
        assert result["passed"]
        assert result["criteria_results"][0]["reason"].startswith("Skipped")

    @pytest.mark.asyncio
    async def test_llm_judge_with_mock_provider(self, validator: OutputValidator, tmp_path: Path) -> None:
        criteria = {
            "criteria": [
                {"name": "code_quality", "type": "llm_judge", "prompt": "Is this quality code?", "weight": 1.0},
            ],
            "threshold": 0.5,
        }
        (tmp_path / "grading.yaml").write_text(yaml.dump(criteria))

        class MockProvider:
            async def chat(self, messages: list, **kwargs: Any) -> dict:
                return {"content": "PASS — code follows best practices"}

        manifest = {"grading_criteria_ref": "grading.yaml"}
        result = await validator.validate(
            {"content": "good code"}, manifest,
            agent_dir=str(tmp_path), provider=MockProvider(),
        )
        assert result["passed"]
        assert result["criteria_results"][0]["passed"]


class TestCheckExpressions:
    """Test the simple expression evaluator in OutputValidator."""

    @pytest.mark.asyncio
    async def test_true_always_passes(self, validator: OutputValidator) -> None:
        assert validator._eval_check("true", {})

    @pytest.mark.asyncio
    async def test_false_always_fails(self, validator: OutputValidator) -> None:
        assert not validator._eval_check("false", {})

    @pytest.mark.asyncio
    async def test_equality_check(self, validator: OutputValidator) -> None:
        assert validator._eval_check("status == success", {"status": "success"})
        assert not validator._eval_check("status == success", {"status": "failure"})

    @pytest.mark.asyncio
    async def test_numeric_comparison(self, validator: OutputValidator) -> None:
        assert validator._eval_check("coverage >= 80", {"coverage": 90})
        assert not validator._eval_check("coverage >= 80", {"coverage": 50})

    @pytest.mark.asyncio
    async def test_exists_check(self, validator: OutputValidator) -> None:
        assert validator._eval_check("test_file_exists", {"test_file": "tests/test_x.py"})

    @pytest.mark.asyncio
    async def test_missing_field_fails(self, validator: OutputValidator) -> None:
        assert not validator._eval_check("count >= 1", {})


class TestIntegrationWithRealManifest:
    """Test with real backend_fastapi agent manifests."""

    @pytest.mark.asyncio
    async def test_codegen_grading_criteria(self) -> None:
        from pathlib import Path
        agent_dir = (
            Path(__file__).parent.parent.parent
            / "examples/backend_fastapi/agents/FastAPICodeGenAgent/v1"
        )
        if not agent_dir.exists():
            pytest.skip("backend_fastapi example not found")

        manifest = yaml.safe_load(
            (agent_dir / "agent_manifest.yaml").read_text()
        )
        validator = OutputValidator()

        # Simulate a good output
        output = {
            "exit_code": 0,
            "type_hint_coverage": 0.96,
            "test_file": "tests/test_orders.py",
            "test_count": 3,
        }
        result = await validator.validate(output, manifest, agent_dir=str(agent_dir))

        assert "criteria_results" in result
        assert len(result["criteria_results"]) > 0
        # Check that automated criteria were evaluated
        auto_results = [c for c in result["criteria_results"] if c["type"] == "automated"]
        assert len(auto_results) >= 1
