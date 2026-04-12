"""Tests for LocalFilesystemStorage (StorageBackend implementation)."""

from __future__ import annotations

import tempfile
from datetime import datetime

import pytest

from agent_contracts.contracts.artifact_record import ArtifactRecord, ArtifactType
from agent_contracts.contracts.checkpoint import Checkpoint
from agent_contracts.contracts.run_record import RunRecord, RunStatus
from harness.core.storage import LocalFilesystemStorage


def _make_run_record(trace_id: str, agent_id: str = "agent-a", domain_id: str = "backend") -> RunRecord:
    return RunRecord(
        trace_id=trace_id,
        task_id="task-1",
        agent_id=agent_id,
        provider="anthropic",
        model="claude-sonnet-4-6",
        status=RunStatus.SUCCESS,
        metadata={"domain_id": domain_id},
    )


def _make_artifact(artifact_id: str, run_path: str) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact_id,
        type=ArtifactType.CODE_DIFF,
        path=run_path,
    )


def _make_checkpoint(workflow_id: str, step: str) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=f"cp-{step}",
        workflow_id=workflow_id,
        step=step,
        state_snapshot={"key": "val"},
    )


class TestLocalFilesystemStorage:
    @pytest.mark.asyncio
    async def test_save_and_load_run_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalFilesystemStorage(base_dir=tmpdir)
            record = _make_run_record("t-1")
            await storage.save_run_record(record)

            results = await storage.query_run_records("backend")
            assert len(results) == 1
            assert results[0].trace_id == "t-1"

    @pytest.mark.asyncio
    async def test_save_and_load_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalFilesystemStorage(base_dir=tmpdir)
            # Use a relative path so the artifact lands inside base_dir
            artifact = _make_artifact("art-1", f"t-1/artifacts/art-1.json")
            await storage.save_artifact(artifact)
            loaded = await storage.load_artifact("art-1")
            assert loaded.artifact_id == "art-1"
            assert loaded.type == ArtifactType.CODE_DIFF

    @pytest.mark.asyncio
    async def test_list_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalFilesystemStorage(base_dir=tmpdir)
            await storage.save_artifact(_make_artifact("art-1", "t-1/artifacts/art-1.json"))
            await storage.save_artifact(_make_artifact("art-2", "t-1/artifacts/art-2.json"))

            arts = await storage.list_artifacts("t-1")
            assert sorted(a.artifact_id for a in arts) == ["art-1", "art-2"]

    @pytest.mark.asyncio
    async def test_list_artifacts_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalFilesystemStorage(base_dir=tmpdir)
            assert await storage.list_artifacts("nonexistent") == []

    @pytest.mark.asyncio
    async def test_save_and_load_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalFilesystemStorage(base_dir=tmpdir)
            cp = _make_checkpoint("wf-1", "step-a")
            await storage.save_checkpoint(cp)
            loaded = await storage.load_checkpoint("wf-1", "step-a")
            assert loaded.checkpoint_id == "cp-step-a"
            assert loaded.state_snapshot == {"key": "val"}

    @pytest.mark.asyncio
    async def test_query_run_records_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalFilesystemStorage(base_dir=tmpdir)
            r1 = _make_run_record("t-1", agent_id="A", domain_id="backend")
            r2 = _make_run_record("t-2", agent_id="B", domain_id="backend")
            r2.status = RunStatus.FAILURE  # type: ignore[misc]
            r3 = _make_run_record("t-3", agent_id="C", domain_id="qa")
            for r in (r1, r2, r3):
                await storage.save_run_record(r)

            backend_results = await storage.query_run_records("backend")
            assert len(backend_results) == 2

            qa_results = await storage.query_run_records("qa")
            assert len(qa_results) == 1
            assert qa_results[0].agent_id == "C"

    @pytest.mark.asyncio
    async def test_query_no_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalFilesystemStorage(base_dir=tmpdir)
            await storage.save_run_record(_make_run_record("t-1"))
            assert await storage.query_run_records("unknown-domain") == []

    @pytest.mark.asyncio
    async def test_load_artifact_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalFilesystemStorage(base_dir=tmpdir)
            with pytest.raises(FileNotFoundError):
                await storage.load_artifact("missing-id")

    @pytest.mark.asyncio
    async def test_load_checkpoint_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalFilesystemStorage(base_dir=tmpdir)
            with pytest.raises(FileNotFoundError):
                await storage.load_checkpoint("wf-x", "step-x")
