"""Tests for durable workflow state — WorkflowStateStore implementations."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from harness.core.workflow_state import FileStateStore, InMemoryStateStore, WorkflowStateStore
from harness.core.composition_engine import CompositionEngine, ExecutionEvent


# ---------------------------------------------------------------------------
# InMemoryStateStore
# ---------------------------------------------------------------------------


class TestInMemoryStateStore:
    def setup_method(self) -> None:
        self.store = InMemoryStateStore()

    def test_save_and_load_step(self) -> None:
        self.store.save_step("wf1", "step_a", {"status": "completed", "output": "hello"})
        result = self.store.load_step("wf1", "step_a")
        assert result == {"status": "completed", "output": "hello"}

    def test_load_missing_step_returns_none(self) -> None:
        assert self.store.load_step("wf1", "nonexistent") is None

    def test_load_missing_workflow_returns_none(self) -> None:
        assert self.store.load_step("no_such_workflow", "step_a") is None

    def test_list_completed_empty(self) -> None:
        assert self.store.list_completed("wf1") == []

    def test_list_completed_after_saves(self) -> None:
        self.store.save_step("wf1", "alpha", {"x": 1})
        self.store.save_step("wf1", "beta", {"x": 2})
        completed = self.store.list_completed("wf1")
        assert set(completed) == {"alpha", "beta"}

    def test_list_completed_isolated_per_workflow(self) -> None:
        self.store.save_step("wf1", "step_a", {})
        self.store.save_step("wf2", "step_b", {})
        assert self.store.list_completed("wf1") == ["step_a"]
        assert self.store.list_completed("wf2") == ["step_b"]

    def test_clear_removes_all_steps(self) -> None:
        self.store.save_step("wf1", "step_a", {})
        self.store.save_step("wf1", "step_b", {})
        self.store.clear("wf1")
        assert self.store.list_completed("wf1") == []
        assert self.store.load_step("wf1", "step_a") is None

    def test_clear_nonexistent_workflow_is_noop(self) -> None:
        self.store.clear("ghost")  # should not raise

    def test_overwrite_existing_step(self) -> None:
        self.store.save_step("wf1", "step_a", {"v": 1})
        self.store.save_step("wf1", "step_a", {"v": 2})
        assert self.store.load_step("wf1", "step_a") == {"v": 2}

    def test_satisfies_protocol(self) -> None:
        assert isinstance(self.store, WorkflowStateStore)


# ---------------------------------------------------------------------------
# FileStateStore
# ---------------------------------------------------------------------------


class TestFileStateStore:
    def test_save_and_load_step(self, tmp_path: Any) -> None:
        store = FileStateStore(base_dir=tmp_path)
        store.save_step("wf1", "step_a", {"status": "completed", "output": "data"})
        result = store.load_step("wf1", "step_a")
        assert result == {"status": "completed", "output": "data"}

    def test_load_missing_step_returns_none(self, tmp_path: Any) -> None:
        store = FileStateStore(base_dir=tmp_path)
        assert store.load_step("wf1", "missing") is None

    def test_load_missing_workflow_returns_none(self, tmp_path: Any) -> None:
        store = FileStateStore(base_dir=tmp_path)
        assert store.load_step("no_wf", "step_a") is None

    def test_list_completed_empty_for_unknown_workflow(self, tmp_path: Any) -> None:
        store = FileStateStore(base_dir=tmp_path)
        assert store.list_completed("wf_unknown") == []

    def test_list_completed_sorted(self, tmp_path: Any) -> None:
        store = FileStateStore(base_dir=tmp_path)
        store.save_step("wf1", "step_c", {})
        store.save_step("wf1", "step_a", {})
        store.save_step("wf1", "step_b", {})
        assert store.list_completed("wf1") == ["step_a", "step_b", "step_c"]

    def test_clear_removes_files_and_dir(self, tmp_path: Any) -> None:
        store = FileStateStore(base_dir=tmp_path)
        store.save_step("wf1", "step_a", {"x": 1})
        store.save_step("wf1", "step_b", {"x": 2})
        store.clear("wf1")
        assert store.list_completed("wf1") == []
        assert store.load_step("wf1", "step_a") is None
        assert not (tmp_path / "wf1").exists()

    def test_clear_nonexistent_workflow_is_noop(self, tmp_path: Any) -> None:
        store = FileStateStore(base_dir=tmp_path)
        store.clear("ghost")  # must not raise

    def test_creates_parent_dirs_automatically(self, tmp_path: Any) -> None:
        store = FileStateStore(base_dir=tmp_path / "deep" / "nested")
        store.save_step("wf1", "step_a", {"ok": True})
        assert store.load_step("wf1", "step_a") == {"ok": True}

    def test_round_trip_complex_nested_data(self, tmp_path: Any) -> None:
        store = FileStateStore(base_dir=tmp_path)
        payload: dict[str, Any] = {
            "status": "completed",
            "output": "some text",
            "metrics": {"score": 0.92, "tokens": 512},
            "tags": ["genesis", "backend"],
            "nested": {"deep": {"value": 42}},
        }
        store.save_step("wf1", "complex_step", payload)
        loaded = store.load_step("wf1", "complex_step")
        assert loaded == payload

    def test_non_serialisable_values_coerced_to_string(self, tmp_path: Any) -> None:
        """default=str in json.dumps must handle non-JSON types without raising."""
        store = FileStateStore(base_dir=tmp_path)
        from pathlib import Path as _Path
        payload = {"path_obj": _Path("/tmp/foo")}
        store.save_step("wf1", "path_step", payload)
        loaded = store.load_step("wf1", "path_step")
        # Path gets coerced to its string representation
        assert loaded == {"path_obj": "/tmp/foo"}

    def test_satisfies_protocol(self, tmp_path: Any) -> None:
        store = FileStateStore(base_dir=tmp_path)
        assert isinstance(store, WorkflowStateStore)


# ---------------------------------------------------------------------------
# DAG resume integration
# ---------------------------------------------------------------------------


class TestDagResume:
    """Verify that execute_dag skips pre-populated steps and executes the rest."""

    @pytest.mark.asyncio
    async def test_pre_populated_step_is_skipped(self) -> None:
        """Step 1 result pre-loaded in store → step 1 NOT re-executed."""
        store = InMemoryStateStore()
        cached_result: dict[str, Any] = {
            "step": "step1",
            "agent": "AgentA",
            "status": "completed",
            "output": "cached output",
            "dependencies": {},
        }
        store.save_step("test-run", "step1", cached_result)

        engine = CompositionEngine(state_store=store)
        steps = [
            {"name": "step1", "agent": "AgentA"},
            {"name": "step2", "agent": "AgentB", "depends_on": ["step1"]},
            {"name": "step3", "agent": "AgentC", "depends_on": ["step2"]},
        ]

        execute_calls: list[str] = []
        original_execute_step = engine._execute_step

        async def tracking_execute_step(
            step_name: str, agent_id: str, config: dict, dep_artifacts: dict
        ) -> dict:
            execute_calls.append(step_name)
            return await original_execute_step(step_name, agent_id, config, dep_artifacts)

        engine._execute_step = tracking_execute_step  # type: ignore[method-assign]

        result = await engine.execute_dag(steps, workflow_id="test-run")

        assert result["status"] == "completed"
        # step1 must NOT have been re-executed
        assert "step1" not in execute_calls
        # step2 and step3 must have been executed
        assert "step2" in execute_calls
        assert "step3" in execute_calls

    @pytest.mark.asyncio
    async def test_resumed_step_result_available_to_dependents(self) -> None:
        """A resumed step's cached result must be visible to downstream steps."""
        store = InMemoryStateStore()
        cached: dict[str, Any] = {
            "step": "step1",
            "agent": "AgentA",
            "status": "completed",
            "output": "upstream data",
            "dependencies": {},
        }
        store.save_step("wf-resume", "step1", cached)

        engine = CompositionEngine(state_store=store)
        steps = [
            {"name": "step1", "agent": "AgentA"},
            {"name": "step2", "agent": "AgentB", "depends_on": ["step1"]},
        ]
        result = await engine.execute_dag(steps, workflow_id="wf-resume")

        assert result["status"] == "completed"
        # Cached result should be exactly the pre-populated value
        assert result["step_results"]["step1"] == cached

    @pytest.mark.asyncio
    async def test_completed_steps_saved_to_store(self) -> None:
        """Freshly executed steps must be persisted so a future resume can use them."""
        store = InMemoryStateStore()
        engine = CompositionEngine(state_store=store)
        steps = [
            {"name": "alpha", "agent": "AgentA"},
            {"name": "beta", "agent": "AgentB", "depends_on": ["alpha"]},
        ]
        await engine.execute_dag(steps, workflow_id="persist-test")

        assert store.load_step("persist-test", "alpha") is not None
        assert store.load_step("persist-test", "beta") is not None

    @pytest.mark.asyncio
    async def test_state_restored_event_logged_for_skipped_steps(self) -> None:
        """STATE_RESTORED event must appear in the execution log for resumed steps."""
        store = InMemoryStateStore()
        store.save_step("wf-log", "step1", {"status": "completed", "output": "x", "dependencies": {}})

        engine = CompositionEngine(state_store=store)
        steps = [
            {"name": "step1", "agent": "AgentA"},
            {"name": "step2", "agent": "AgentB", "depends_on": ["step1"]},
        ]
        result = await engine.execute_dag(steps, workflow_id="wf-log")

        restored_events = [
            e for e in result["execution_log"]
            if e.get("event") == ExecutionEvent.STATE_RESTORED
        ]
        assert len(restored_events) == 1
        assert restored_events[0]["step"] == "step1"

    @pytest.mark.asyncio
    async def test_default_state_store_is_in_memory(self) -> None:
        """Omitting state_store must not change existing observable behaviour."""
        engine = CompositionEngine()
        steps = [{"name": "s1", "agent": "A"}, {"name": "s2", "agent": "B"}]
        result = await engine.execute_dag(steps)
        assert result["status"] == "completed"
        assert set(result["step_results"]) == {"s1", "s2"}

    @pytest.mark.asyncio
    async def test_all_steps_skipped_when_fully_cached(self) -> None:
        """If all steps are in the store the DAG completes without executing anything."""
        store = InMemoryStateStore()
        for name in ("s1", "s2", "s3"):
            store.save_step("full-cache", name, {"status": "completed", "output": name, "dependencies": {}})

        engine = CompositionEngine(state_store=store)
        execute_calls: list[str] = []
        original = engine._execute_step

        async def tracking(step_name: str, agent_id: str, config: dict, dep: dict) -> dict:
            execute_calls.append(step_name)
            return await original(step_name, agent_id, config, dep)

        engine._execute_step = tracking  # type: ignore[method-assign]

        steps = [
            {"name": "s1", "agent": "A"},
            {"name": "s2", "agent": "B", "depends_on": ["s1"]},
            {"name": "s3", "agent": "C", "depends_on": ["s2"]},
        ]
        result = await engine.execute_dag(steps, workflow_id="full-cache")

        assert result["status"] == "completed"
        assert execute_calls == []

    @pytest.mark.asyncio
    async def test_clear_then_rerun_executes_all_steps(self) -> None:
        """After clearing the store a rerun must execute every step fresh."""
        store = InMemoryStateStore()
        store.save_step("wf-clear", "step1", {"status": "completed", "output": "old", "dependencies": {}})
        store.clear("wf-clear")

        engine = CompositionEngine(state_store=store)
        execute_calls: list[str] = []
        original = engine._execute_step

        async def tracking(step_name: str, agent_id: str, config: dict, dep: dict) -> dict:
            execute_calls.append(step_name)
            return await original(step_name, agent_id, config, dep)

        engine._execute_step = tracking  # type: ignore[method-assign]

        steps = [{"name": "step1", "agent": "AgentA"}]
        result = await engine.execute_dag(steps, workflow_id="wf-clear")

        assert result["status"] == "completed"
        assert "step1" in execute_calls
