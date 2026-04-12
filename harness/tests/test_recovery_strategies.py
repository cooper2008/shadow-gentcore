"""Tests for gate recovery strategies: retry, retry_fresh, rollback."""

from __future__ import annotations

from typing import Any

import pytest

from harness.core.composition_engine import CompositionEngine, GateFailure


class TestRetryWithFeedback:
    """Strategy: retry — inject feedback, keep context, try again."""

    @pytest.mark.asyncio
    async def test_retry_passes_on_second_attempt(self) -> None:
        call_count = 0

        class RetryRunner:
            async def run(self, manifest: Any = None, task: Any = None,
                          system_prompt_content: str = "",
                          context_items: list | None = None) -> dict[str, Any]:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return {"status": "failed", "output": "first attempt failed"}
                return {"status": "completed", "output": "fixed on retry"}

        engine = CompositionEngine(agent_runner=RetryRunner())
        steps = [{"name": "code", "agent": "x/A/v1",
                  "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}}]
        configs = {"code": {"manifest": {"id": "A"}, "task": {}, "system_prompt": "", "context_items": []}}

        result = await engine.execute(steps, configs)
        assert result["status"] == "completed"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_includes_feedback_in_context(self) -> None:
        captured_contexts: list[list[dict]] = []

        class CapturingRunner:
            async def run(self, manifest: Any = None, task: Any = None,
                          system_prompt_content: str = "",
                          context_items: list | None = None) -> dict[str, Any]:
                captured_contexts.append(list(context_items or []))
                if len(captured_contexts) == 1:
                    return {"status": "failed", "output": "bad"}
                return {"status": "completed", "output": "good"}

        engine = CompositionEngine(agent_runner=CapturingRunner())
        steps = [{"name": "code", "agent": "x/A/v1",
                  "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 1}}]
        configs = {"code": {"manifest": {"id": "A"}, "task": {}, "system_prompt": "", "context_items": []}}

        await engine.execute(steps, configs)
        # Second call should have feedback context
        assert len(captured_contexts) == 2
        retry_sources = [c["source"] for c in captured_contexts[1]]
        assert any("gate_feedback" in s for s in retry_sources)
        # Feedback should say "different approach"
        retry_content = " ".join(c["content"] for c in captured_contexts[1])
        assert "different approach" in retry_content.lower()

    @pytest.mark.asyncio
    async def test_retry_preserves_dependency_context(self) -> None:
        """Retries should still have upstream step outputs in context."""
        captured: list[list[dict]] = []
        call_count = 0

        class Runner:
            async def run(self, manifest: Any = None, task: Any = None,
                          system_prompt_content: str = "",
                          context_items: list | None = None) -> dict[str, Any]:
                nonlocal call_count
                call_count += 1
                captured.append(list(context_items or []))
                agent_id = (manifest or {}).get("id", "")
                if agent_id == "B" and call_count <= 2:
                    return {"status": "failed", "output": "B failed"}
                return {"status": "completed", "output": f"output_{agent_id}"}

        engine = CompositionEngine(agent_runner=Runner())
        steps = [
            {"name": "step_a", "agent": "x/A/v1"},
            {"name": "step_b", "agent": "x/B/v1", "depends_on": ["step_a"],
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 1}},
        ]
        configs = {
            "step_a": {"manifest": {"id": "A"}, "task": {}, "system_prompt": "", "context_items": []},
            "step_b": {"manifest": {"id": "B"}, "task": {}, "system_prompt": "", "context_items": []},
        }

        result = await engine.execute(steps, configs)
        assert result["status"] == "completed"
        # step_b's retry should have step_a output in context
        last_b_context = captured[-1]
        sources = [c["source"] for c in last_b_context]
        assert any("prior_step:step_a" in s for s in sources)


class TestRetryFresh:
    """Strategy: retry_fresh — clear context, start from scratch."""

    @pytest.mark.asyncio
    async def test_fresh_clears_accumulated_context(self) -> None:
        captured: list[list[dict]] = []

        class Runner:
            async def run(self, manifest: Any = None, task: Any = None,
                          system_prompt_content: str = "",
                          context_items: list | None = None) -> dict[str, Any]:
                captured.append(list(context_items or []))
                if len(captured) == 1:
                    return {"status": "failed", "output": "went in wrong direction " * 50}
                return {"status": "completed", "output": "fresh approach worked"}

        engine = CompositionEngine(agent_runner=Runner())
        steps = [{"name": "code", "agent": "x/A/v1",
                  "gate": {"condition": "status == success", "on_fail": "retry_fresh", "max_retries": 1}}]
        configs = {"code": {"manifest": {"id": "A"}, "task": {}, "system_prompt": "",
                            "context_items": [{"source": "original", "content": "original context", "priority": 5}]}}

        await engine.execute(steps, configs)
        assert len(captured) == 2

        # Fresh retry should have original context + recovery note, NOT accumulated failure output
        retry_ctx = captured[1]
        sources = [c["source"] for c in retry_ctx]
        assert any("original" in s for s in sources), "Original context should be preserved"
        assert any("recovery" in s for s in sources), "Recovery note should be present"
        # Should NOT have the massive failed output in context
        total_content = " ".join(c["content"] for c in retry_ctx)
        assert "went in wrong direction" not in total_content

    @pytest.mark.asyncio
    async def test_fresh_retry_passes(self) -> None:
        call_count = 0

        class Runner:
            async def run(self, **kwargs: Any) -> dict[str, Any]:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return {"status": "failed", "output": "completely wrong"}
                return {"status": "completed", "output": "fresh start worked"}

        engine = CompositionEngine(agent_runner=Runner())
        steps = [{"name": "code", "agent": "x/A/v1",
                  "gate": {"condition": "status == success", "on_fail": "retry_fresh", "max_retries": 1}}]
        configs = {"code": {"manifest": {"id": "A"}, "task": {}, "system_prompt": "", "context_items": []}}

        result = await engine.execute(steps, configs)
        assert result["status"] == "completed"


class TestRollback:
    """Strategy: rollback — clear results from a point, re-run from there."""

    @pytest.mark.asyncio
    async def test_rollback_clears_step_results(self) -> None:
        call_count = 0

        class Runner:
            async def run(self, manifest: Any = None, **kwargs: Any) -> dict[str, Any]:
                nonlocal call_count
                call_count += 1
                agent_id = (manifest or {}).get("id", "")
                if agent_id == "B" and call_count <= 2:
                    return {"status": "failed", "output": "B fails"}
                return {"status": "completed", "output": f"ok_{agent_id}"}

        engine = CompositionEngine(agent_runner=Runner())
        steps = [
            {"name": "step_a", "agent": "x/A/v1"},
            {"name": "step_b", "agent": "x/B/v1", "depends_on": ["step_a"],
             "gate": {"condition": "status == success", "on_fail": "rollback",
                      "rollback_to": "step_a"}},
        ]
        configs = {
            "step_a": {"manifest": {"id": "A"}, "task": {}, "system_prompt": "", "context_items": []},
            "step_b": {"manifest": {"id": "B"}, "task": {}, "system_prompt": "", "context_items": []},
        }

        result = await engine.execute(steps, configs)
        # Rollback returns gate_failed — the caller would need to re-execute
        assert result["status"] == "gate_failed"
        # step_a should have been cleared from results (rollback)
        assert "step_a" not in engine.step_results


class TestExhaustionBehavior:
    """When all retries are exhausted."""

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_returns_failed(self) -> None:
        class AlwaysFailRunner:
            async def run(self, **kwargs: Any) -> dict[str, Any]:
                return {"status": "failed", "output": "always fails"}

        engine = CompositionEngine(agent_runner=AlwaysFailRunner())
        steps = [{"name": "code", "agent": "x/A/v1",
                  "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 3}}]
        configs = {"code": {"manifest": {"id": "A"}, "task": {}, "system_prompt": "", "context_items": []}}

        result = await engine.execute(steps, configs)
        assert result["status"] == "gate_failed"

        # Should have logged all 3 retry attempts
        retries = [e for e in engine.execution_log if e.get("event") == "gate_retry"]
        assert len(retries) == 3

    @pytest.mark.asyncio
    async def test_fresh_retries_also_exhaust(self) -> None:
        class AlwaysFailRunner:
            async def run(self, **kwargs: Any) -> dict[str, Any]:
                return {"status": "failed", "output": "fails"}

        engine = CompositionEngine(agent_runner=AlwaysFailRunner())
        steps = [{"name": "code", "agent": "x/A/v1",
                  "gate": {"condition": "status == success", "on_fail": "retry_fresh", "max_retries": 2}}]
        configs = {"code": {"manifest": {"id": "A"}, "task": {}, "system_prompt": "", "context_items": []}}

        result = await engine.execute(steps, configs)
        assert result["status"] == "gate_failed"

        fresh_retries = [e for e in engine.execution_log if e.get("event") == "gate_retry_fresh"]
        assert len(fresh_retries) == 2


class TestAuditTrail:
    """Recovery decisions should be fully auditable."""

    @pytest.mark.asyncio
    async def test_retry_logged_with_strategy(self) -> None:
        call_count = 0

        class Runner:
            async def run(self, **kwargs: Any) -> dict[str, Any]:
                nonlocal call_count
                call_count += 1
                if call_count <= 2:
                    return {"status": "failed", "output": "fail"}
                return {"status": "completed", "output": "ok"}

        engine = CompositionEngine(agent_runner=Runner())
        steps = [{"name": "code", "agent": "x/A/v1",
                  "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 3}}]
        configs = {"code": {"manifest": {"id": "A"}, "task": {}, "system_prompt": "", "context_items": []}}

        await engine.execute(steps, configs)

        log = engine.execution_log
        # Should have: step_started, gate_failed, gate_retry(1), gate_retry(2), gate_passed_on_retry
        events = [e["event"] for e in log]
        assert "gate_failed" in events
        assert "gate_retry" in events
        assert "gate_passed_on_retry" in events
