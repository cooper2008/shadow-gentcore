"""Tests for the Human Approval Gate feature.

Covers:
- Approval gate with pending status raises HumanApprovalRequired
- After approval, gate passes normally on resume
- List approvals returns pending items
- Approve endpoint marks approval as approved
- Double-approve returns 409
- Non-existent approval returns 404
- Approval with timeout_seconds stored correctly
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.core.composition_engine import (
    CompositionEngine,
    HumanApprovalRequired,
)
from harness.core.workflow_state import FileStateStore, InMemoryStateStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _approval_step(
    step_name: str = "code_review",
    message: str = "Please review",
    timeout_seconds: int = 86400,
) -> tuple[list[dict], dict]:
    """Return a single-step workflow with an approval gate."""
    steps = [{"name": step_name, "agent": "ReviewAgent"}]
    step_configs = {step_name: {"mock_output": "Output ready for review"}}
    steps[0]["gate"] = {
        "type": "approval",
        "message": message,
        "timeout_seconds": timeout_seconds,
    }
    return steps, step_configs


# ---------------------------------------------------------------------------
# 1. Pending approval raises HumanApprovalRequired
# ---------------------------------------------------------------------------

class TestApprovalGatePending:
    @pytest.mark.asyncio
    async def test_raises_human_approval_required_on_first_encounter(self) -> None:
        store = InMemoryStateStore()
        engine = CompositionEngine(state_store=store)
        steps, configs = _approval_step()

        with pytest.raises(HumanApprovalRequired) as exc_info:
            await engine.execute_dag(steps, configs, workflow_id="wf-001")

        exc = exc_info.value
        assert exc.workflow_id == "wf-001"
        assert exc.step_name == "code_review"
        assert "review" in exc.message.lower() or "approval" in exc.message.lower()

    @pytest.mark.asyncio
    async def test_approval_record_persisted_as_pending(self) -> None:
        store = InMemoryStateStore()
        engine = CompositionEngine(state_store=store)
        steps, configs = _approval_step(message="Check the output")

        with pytest.raises(HumanApprovalRequired):
            await engine.execute_dag(steps, configs, workflow_id="wf-002")

        data = store.load_step("wf-002", "_approval_code_review")
        assert data is not None
        assert data["status"] == "pending"
        assert data["message"] == "Check the output"
        assert data["workflow_id"] == "wf-002"
        assert data["step_name"] == "code_review"

    @pytest.mark.asyncio
    async def test_result_preview_stored_in_approval_record(self) -> None:
        store = InMemoryStateStore()
        engine = CompositionEngine(state_store=store)
        steps, configs = _approval_step()

        with pytest.raises(HumanApprovalRequired):
            await engine.execute_dag(steps, configs, workflow_id="wf-003")

        data = store.load_step("wf-003", "_approval_code_review")
        assert data is not None
        assert "result_preview" in data

    @pytest.mark.asyncio
    async def test_created_at_is_a_float_timestamp(self) -> None:
        store = InMemoryStateStore()
        engine = CompositionEngine(state_store=store)
        steps, configs = _approval_step()
        before = time.time()

        with pytest.raises(HumanApprovalRequired):
            await engine.execute_dag(steps, configs, workflow_id="wf-004")

        after = time.time()
        data = store.load_step("wf-004", "_approval_code_review")
        assert data is not None
        assert before <= data["created_at"] <= after

    @pytest.mark.asyncio
    async def test_exception_message_contains_workflow_and_step(self) -> None:
        store = InMemoryStateStore()
        engine = CompositionEngine(state_store=store)
        steps, configs = _approval_step()

        with pytest.raises(HumanApprovalRequired) as exc_info:
            await engine.execute_dag(steps, configs, workflow_id="wf-msg-test")

        assert "wf-msg-test" in str(exc_info.value)
        assert "code_review" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. timeout_seconds stored correctly
# ---------------------------------------------------------------------------

class TestApprovalTimeoutSeconds:
    @pytest.mark.asyncio
    async def test_custom_timeout_stored_in_approval_record(self) -> None:
        store = InMemoryStateStore()
        engine = CompositionEngine(state_store=store)
        steps, configs = _approval_step(timeout_seconds=3600)

        with pytest.raises(HumanApprovalRequired):
            await engine.execute_dag(steps, configs, workflow_id="wf-timeout")

        data = store.load_step("wf-timeout", "_approval_code_review")
        assert data is not None
        assert data["timeout_seconds"] == 3600

    @pytest.mark.asyncio
    async def test_default_timeout_is_86400(self) -> None:
        store = InMemoryStateStore()
        engine = CompositionEngine(state_store=store)
        steps = [{"name": "step_a", "agent": "A"}]
        step_configs = {"step_a": {"mock_output": "done"}}
        # Gate without explicit timeout_seconds
        steps[0]["gate"] = {"type": "approval", "message": "Please review"}

        with pytest.raises(HumanApprovalRequired):
            await engine.execute_dag(steps, step_configs, workflow_id="wf-default-timeout")

        data = store.load_step("wf-default-timeout", "_approval_step_a")
        assert data is not None
        assert data["timeout_seconds"] == 86400


# ---------------------------------------------------------------------------
# 3. After approval, gate passes on resume
# ---------------------------------------------------------------------------

class TestApprovalGateResume:
    @pytest.mark.asyncio
    async def test_approved_gate_passes_on_resume(self) -> None:
        store = InMemoryStateStore()
        engine = CompositionEngine(state_store=store)
        steps, configs = _approval_step()

        # First run — triggers approval pause
        with pytest.raises(HumanApprovalRequired):
            await engine.execute_dag(steps, configs, workflow_id="wf-resume")

        # Simulate human approval
        approval_data = store.load_step("wf-resume", "_approval_code_review")
        assert approval_data is not None
        approval_data["status"] = "approved"
        approval_data["approved_at"] = time.time()
        store.save_step("wf-resume", "_approval_code_review", approval_data)

        # Resume: create a fresh engine (simulates server restart / new request)
        engine2 = CompositionEngine(state_store=store)
        result = await engine2.execute_dag(steps, configs, workflow_id="wf-resume")

        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_approved_gate_does_not_raise_on_second_run(self) -> None:
        store = InMemoryStateStore()
        engine = CompositionEngine(state_store=store)
        steps, configs = _approval_step()

        with pytest.raises(HumanApprovalRequired):
            await engine.execute_dag(steps, configs, workflow_id="wf-no-raise")

        # Approve
        data = store.load_step("wf-no-raise", "_approval_code_review")
        data["status"] = "approved"
        data["approved_at"] = time.time()
        store.save_step("wf-no-raise", "_approval_code_review", data)

        engine2 = CompositionEngine(state_store=store)
        # Must not raise
        result = await engine2.execute_dag(steps, configs, workflow_id="wf-no-raise")
        assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 4. HumanApprovalRequired exception attributes
# ---------------------------------------------------------------------------

class TestHumanApprovalRequiredException:
    def test_exception_stores_workflow_id(self) -> None:
        exc = HumanApprovalRequired("wf-123", "step_x", "Review needed")
        assert exc.workflow_id == "wf-123"

    def test_exception_stores_step_name(self) -> None:
        exc = HumanApprovalRequired("wf-123", "step_x", "Review needed")
        assert exc.step_name == "step_x"

    def test_exception_stores_message(self) -> None:
        exc = HumanApprovalRequired("wf-123", "step_x", "Review needed")
        assert exc.message == "Review needed"

    def test_exception_default_message_is_empty_string(self) -> None:
        exc = HumanApprovalRequired("wf-123", "step_x")
        assert exc.message == ""

    def test_str_contains_workflow_id_and_step(self) -> None:
        exc = HumanApprovalRequired("wf-abc", "my_step", "check it")
        text = str(exc)
        assert "wf-abc" in text
        assert "my_step" in text


# ---------------------------------------------------------------------------
# 5. Server endpoints — list approvals / approve / error cases
# ---------------------------------------------------------------------------

import contextlib
import importlib


@contextlib.contextmanager
def _make_client(**env_overrides):
    """Yield a FastAPI TestClient with controlled env vars."""
    import harness.server.app as app_mod

    clean = {k: v for k, v in os.environ.items()
             if k not in ("AGENT_API_KEY", "AGENT_AUTH_DISABLED")}
    clean.update(env_overrides)

    with patch.dict(os.environ, clean, clear=True):
        importlib.reload(app_mod)
        from fastapi.testclient import TestClient
        yield TestClient(app_mod.create_app())


class TestListApprovalsEndpoint:
    def test_list_approvals_returns_empty_for_unknown_workflow(self, tmp_path: Path) -> None:
        with _make_client(AGENT_AUTH_DISABLED="true", DOMAIN_PATH=str(tmp_path)) as client:
            resp = client.get("/workflows/no-such-wf/approvals")
            assert resp.status_code == 200
            assert resp.json() == []

    def test_list_approvals_returns_pending_items(self, tmp_path: Path) -> None:
        store = FileStateStore(tmp_path / ".gentcore" / "state")
        approval = {
            "workflow_id": "wf-list",
            "step_name": "review",
            "status": "pending",
            "message": "Please look",
            "result_preview": "some output",
            "created_at": time.time(),
            "timeout_seconds": 86400,
        }
        store.save_step("wf-list", "_approval_review", approval)

        with _make_client(AGENT_AUTH_DISABLED="true", DOMAIN_PATH=str(tmp_path)) as client:
            resp = client.get("/workflows/wf-list/approvals")
            assert resp.status_code == 200
            items = resp.json()
            assert len(items) == 1
            assert items[0]["step_name"] == "review"
            assert items[0]["status"] == "pending"

    def test_list_approvals_excludes_approved_items(self, tmp_path: Path) -> None:
        store = FileStateStore(tmp_path / ".gentcore" / "state")
        approved = {
            "workflow_id": "wf-excl",
            "step_name": "done_step",
            "status": "approved",
            "message": "Already done",
            "result_preview": "",
            "created_at": time.time(),
            "timeout_seconds": 86400,
        }
        store.save_step("wf-excl", "_approval_done_step", approved)

        with _make_client(AGENT_AUTH_DISABLED="true", DOMAIN_PATH=str(tmp_path)) as client:
            resp = client.get("/workflows/wf-excl/approvals")
            assert resp.status_code == 200
            assert resp.json() == []

    def test_list_approvals_requires_auth(self, tmp_path: Path) -> None:
        with _make_client(AGENT_API_KEY="secret", DOMAIN_PATH=str(tmp_path)) as client:
            resp = client.get("/workflows/wf-x/approvals")
            assert resp.status_code == 401


class TestApproveStepEndpoint:
    def test_approve_pending_step_returns_200(self, tmp_path: Path) -> None:
        store = FileStateStore(tmp_path / ".gentcore" / "state")
        approval = {
            "workflow_id": "wf-approve",
            "step_name": "code_review",
            "status": "pending",
            "message": "Review needed",
            "result_preview": "output here",
            "created_at": time.time(),
            "timeout_seconds": 86400,
        }
        store.save_step("wf-approve", "_approval_code_review", approval)

        with _make_client(AGENT_AUTH_DISABLED="true", DOMAIN_PATH=str(tmp_path)) as client:
            resp = client.post("/workflows/wf-approve/approve/code_review")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "approved"
            assert body["workflow_id"] == "wf-approve"
            assert body["step_name"] == "code_review"

    def test_approve_updates_status_to_approved_in_store(self, tmp_path: Path) -> None:
        store = FileStateStore(tmp_path / ".gentcore" / "state")
        approval = {
            "workflow_id": "wf-upd",
            "step_name": "my_step",
            "status": "pending",
            "message": "Check",
            "result_preview": "",
            "created_at": time.time(),
            "timeout_seconds": 86400,
        }
        store.save_step("wf-upd", "_approval_my_step", approval)

        with _make_client(AGENT_AUTH_DISABLED="true", DOMAIN_PATH=str(tmp_path)) as client:
            client.post("/workflows/wf-upd/approve/my_step")

        updated = store.load_step("wf-upd", "_approval_my_step")
        assert updated is not None
        assert updated["status"] == "approved"
        assert "approved_at" in updated

    def test_approve_nonexistent_step_returns_404(self, tmp_path: Path) -> None:
        with _make_client(AGENT_AUTH_DISABLED="true", DOMAIN_PATH=str(tmp_path)) as client:
            resp = client.post("/workflows/wf-404/approve/ghost_step")
            assert resp.status_code == 404
            assert "ghost_step" in resp.json()["detail"]

    def test_double_approve_returns_409(self, tmp_path: Path) -> None:
        store = FileStateStore(tmp_path / ".gentcore" / "state")
        already_approved = {
            "workflow_id": "wf-dbl",
            "step_name": "step_a",
            "status": "approved",
            "message": "Done",
            "result_preview": "",
            "created_at": time.time(),
            "timeout_seconds": 86400,
            "approved_at": time.time(),
        }
        store.save_step("wf-dbl", "_approval_step_a", already_approved)

        with _make_client(AGENT_AUTH_DISABLED="true", DOMAIN_PATH=str(tmp_path)) as client:
            resp = client.post("/workflows/wf-dbl/approve/step_a")
            assert resp.status_code == 409
            assert "approved" in resp.json()["detail"].lower()

    def test_approve_requires_auth(self, tmp_path: Path) -> None:
        with _make_client(AGENT_API_KEY="secret", DOMAIN_PATH=str(tmp_path)) as client:
            resp = client.post("/workflows/wf-y/approve/step_z")
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 6. Standard gates are not affected (regression guard)
# ---------------------------------------------------------------------------

class TestStandardGateRegressionWithApprovalFeature:
    @pytest.mark.asyncio
    async def test_standard_gate_still_passes(self) -> None:
        engine = CompositionEngine()
        steps = [{"name": "s1", "agent": "A", "gate": {"condition": "true"}}]
        result = await engine.execute_dag(steps, {}, workflow_id="wf-std")
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_router_gate_still_works(self) -> None:
        engine = CompositionEngine()
        steps = [
            {
                "name": "classify",
                "agent": "Classifier",
                "gate": {
                    "type": "router",
                    "routes": [
                        {"condition": "output contains Output", "next_step": "review"},
                        {"default": True, "next_step": "skip"},
                    ],
                },
            }
        ]
        configs = {"classify": {"mock_output": "Output from classify"}}
        result = await engine.execute_dag(steps, configs, workflow_id="wf-router")
        assert result["status"] == "completed"
        assert result["step_results"]["classify"]["_routed_to"] == "review"
