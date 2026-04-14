"""Tests for the router gate type in CompositionEngine."""

from __future__ import annotations

import logging

import pytest

from harness.core.composition_engine import CompositionEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engine_with_output(output: str, status: str = "completed", confidence: float = 0.5) -> CompositionEngine:
    """Return a CompositionEngine whose stub produces a fixed output."""
    engine = CompositionEngine()
    # Patch the stub result injected by _execute_step when no agent_runner present
    # by using mock_output in the step config.
    return engine


def _step_with_router(routes: list[dict], mock_output: str = "") -> tuple[list[dict], dict]:
    """Build a single-step workflow with a router gate."""
    steps = [{"name": "classify", "agent": "Classifier"}]
    step_configs = {
        "classify": {
            "mock_output": mock_output,
        }
    }
    steps[0]["gate"] = {"type": "router", "routes": routes}
    return steps, step_configs


# ---------------------------------------------------------------------------
# Router condition: "output contains"
# ---------------------------------------------------------------------------

class TestRouterOutputContains:
    @pytest.mark.asyncio
    async def test_routes_to_code_review_when_output_contains_code(self) -> None:
        steps, configs = _step_with_router(
            routes=[
                {"condition": "output contains code", "next_step": "code_review"},
                {"condition": "output contains docs", "next_step": "docs_review"},
                {"default": True, "next_step": "general_review"},
            ],
            mock_output="Here is some Python code for you.",
        )
        engine = CompositionEngine()
        result = await engine.execute(steps, configs)

        assert result["status"] == "completed"
        assert result["step_results"]["classify"]["_routed_to"] == "code_review"

    @pytest.mark.asyncio
    async def test_routes_to_docs_review_when_output_contains_docs(self) -> None:
        steps, configs = _step_with_router(
            routes=[
                {"condition": "output contains code", "next_step": "code_review"},
                {"condition": "output contains docs", "next_step": "docs_review"},
                {"default": True, "next_step": "general_review"},
            ],
            mock_output="These docs explain the architecture.",
        )
        engine = CompositionEngine()
        result = await engine.execute(steps, configs)

        assert result["status"] == "completed"
        assert result["step_results"]["classify"]["_routed_to"] == "docs_review"

    @pytest.mark.asyncio
    async def test_case_insensitive_output_contains(self) -> None:
        steps, configs = _step_with_router(
            routes=[
                {"condition": "output contains CODE", "next_step": "code_review"},
            ],
            mock_output="Here is some code.",
        )
        engine = CompositionEngine()
        result = await engine.execute(steps, configs)

        assert result["status"] == "completed"
        assert result["step_results"]["classify"]["_routed_to"] == "code_review"


# ---------------------------------------------------------------------------
# Default route
# ---------------------------------------------------------------------------

class TestRouterDefaultRoute:
    @pytest.mark.asyncio
    async def test_default_route_used_when_no_condition_matches(self) -> None:
        steps, configs = _step_with_router(
            routes=[
                {"condition": "output contains code", "next_step": "code_review"},
                {"condition": "output contains docs", "next_step": "docs_review"},
                {"default": True, "next_step": "general_review"},
            ],
            mock_output="Totally unrelated output.",
        )
        engine = CompositionEngine()
        result = await engine.execute(steps, configs)

        assert result["status"] == "completed"
        assert result["step_results"]["classify"]["_routed_to"] == "general_review"

    @pytest.mark.asyncio
    async def test_first_match_wins_over_default(self) -> None:
        """Default route should NOT fire when a prior condition matches."""
        steps, configs = _step_with_router(
            routes=[
                {"condition": "output contains code", "next_step": "code_review"},
                {"default": True, "next_step": "general_review"},
            ],
            mock_output="code snippet here",
        )
        engine = CompositionEngine()
        result = await engine.execute(steps, configs)

        assert result["step_results"]["classify"]["_routed_to"] == "code_review"


# ---------------------------------------------------------------------------
# Multiple routes — first match wins
# ---------------------------------------------------------------------------

class TestRouterFirstMatchWins:
    @pytest.mark.asyncio
    async def test_first_matching_route_is_selected(self) -> None:
        """When output matches multiple conditions, the first listed wins."""
        steps, configs = _step_with_router(
            routes=[
                {"condition": "output contains code", "next_step": "code_review"},
                {"condition": "output contains snippet", "next_step": "snippet_review"},
            ],
            mock_output="code snippet",
        )
        engine = CompositionEngine()
        result = await engine.execute(steps, configs)

        assert result["step_results"]["classify"]["_routed_to"] == "code_review"


# ---------------------------------------------------------------------------
# "confidence >= N" route condition
# ---------------------------------------------------------------------------

class TestRouterConfidenceCondition:
    @pytest.mark.asyncio
    async def test_confidence_route_passes_when_threshold_met(self) -> None:
        steps = [{"name": "classify", "agent": "Classifier"}]
        # Inject confidence directly into the stub result via mock; we override
        # step results manually after execution by checking _evaluate_route_condition.
        # For a cleaner test, set confidence in the result by using a mock engine.
        engine = CompositionEngine()
        # Directly test _evaluate_route_condition with a synthetic result
        result_high = {"output": "some output", "_confidence": 0.9}
        assert engine._evaluate_route_condition("confidence >= 0.8", result_high) is True

    @pytest.mark.asyncio
    async def test_confidence_route_fails_when_below_threshold(self) -> None:
        engine = CompositionEngine()
        result_low = {"output": "some output", "_confidence": 0.5}
        assert engine._evaluate_route_condition("confidence >= 0.8", result_low) is False

    @pytest.mark.asyncio
    async def test_confidence_falls_back_to_confidence_key(self) -> None:
        engine = CompositionEngine()
        result = {"output": "x", "confidence": 0.85}
        assert engine._evaluate_route_condition("confidence >= 0.8", result) is True


# ---------------------------------------------------------------------------
# "status == <value>" route condition
# ---------------------------------------------------------------------------

class TestRouterStatusCondition:
    @pytest.mark.asyncio
    async def test_status_condition_matches_exact_value(self) -> None:
        engine = CompositionEngine()
        result = {"status": "completed", "output": "done"}
        assert engine._evaluate_route_condition("status == completed", result) is True

    @pytest.mark.asyncio
    async def test_status_condition_fails_on_mismatch(self) -> None:
        engine = CompositionEngine()
        result = {"status": "error", "output": ""}
        assert engine._evaluate_route_condition("status == completed", result) is False


# ---------------------------------------------------------------------------
# No routes and no default → gate fails
# ---------------------------------------------------------------------------

class TestRouterNoMatchNoDefault:
    @pytest.mark.asyncio
    async def test_gate_fails_when_no_routes_and_no_default(self) -> None:
        steps, configs = _step_with_router(
            routes=[
                {"condition": "output contains code", "next_step": "code_review"},
            ],
            mock_output="documentation text",
        )
        engine = CompositionEngine()
        result = await engine.execute(steps, configs)

        assert result["status"] == "gate_failed"
        assert "classify" == result.get("failed_step")

    @pytest.mark.asyncio
    async def test_gate_fails_with_empty_routes_list(self) -> None:
        steps, configs = _step_with_router(routes=[], mock_output="anything")
        engine = CompositionEngine()
        result = await engine.execute(steps, configs)

        assert result["status"] == "gate_failed"


# ---------------------------------------------------------------------------
# Unknown route condition → logged warning, condition skipped (returns False)
# ---------------------------------------------------------------------------

class TestRouterUnknownCondition:
    @pytest.mark.asyncio
    async def test_unknown_condition_logs_warning_and_returns_false(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        engine = CompositionEngine()
        result = {"output": "some output"}
        with caplog.at_level(logging.WARNING, logger="harness.core.composition_engine"):
            matched = engine._evaluate_route_condition("some_unknown_condition", result)
        assert matched is False
        assert "Unknown router condition" in caplog.text

    @pytest.mark.asyncio
    async def test_unknown_condition_skipped_falls_through_to_default(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        steps, configs = _step_with_router(
            routes=[
                {"condition": "some_unknown_condition", "next_step": "mystery"},
                {"default": True, "next_step": "general_review"},
            ],
            mock_output="output",
        )
        engine = CompositionEngine()
        with caplog.at_level(logging.WARNING, logger="harness.core.composition_engine"):
            result = await engine.execute(steps, configs)

        assert result["status"] == "completed"
        assert result["step_results"]["classify"]["_routed_to"] == "general_review"


# ---------------------------------------------------------------------------
# _routed_to stored in result dict
# ---------------------------------------------------------------------------

class TestRouterResultStorage:
    @pytest.mark.asyncio
    async def test_routed_to_is_stored_in_step_result(self) -> None:
        steps, configs = _step_with_router(
            routes=[
                {"condition": "output contains code", "next_step": "code_review"},
            ],
            mock_output="code example",
        )
        engine = CompositionEngine()
        result = await engine.execute(steps, configs)

        classify_result = result["step_results"]["classify"]
        assert "_routed_to" in classify_result
        assert classify_result["_routed_to"] == "code_review"

    @pytest.mark.asyncio
    async def test_routed_to_stored_for_default_route(self) -> None:
        steps, configs = _step_with_router(
            routes=[
                {"condition": "output contains code", "next_step": "code_review"},
                {"default": True, "next_step": "general_review"},
            ],
            mock_output="nothing relevant",
        )
        engine = CompositionEngine()
        result = await engine.execute(steps, configs)

        classify_result = result["step_results"]["classify"]
        assert classify_result.get("_routed_to") == "general_review"


# ---------------------------------------------------------------------------
# Standard gates are unaffected (regression guard)
# ---------------------------------------------------------------------------

class TestStandardGateRegression:
    @pytest.mark.asyncio
    async def test_standard_gate_type_explicit_still_works(self) -> None:
        steps = [
            {
                "name": "step1",
                "agent": "A",
                "gate": {"type": "standard", "condition": "true"},
            }
        ]
        engine = CompositionEngine()
        result = await engine.execute(steps)
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_gate_without_type_still_defaults_to_standard(self) -> None:
        steps = [
            {
                "name": "step1",
                "agent": "A",
                "gate": {"condition": "true"},
            }
        ]
        engine = CompositionEngine()
        result = await engine.execute(steps)
        assert result["status"] == "completed"
