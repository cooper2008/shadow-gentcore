"""Tests for HandoffManager."""

from __future__ import annotations

import json

import pytest

from harness.core.handoff import HandoffManager


class TestHandoffManager:
    def test_create_checkpoint(self) -> None:
        hm = HandoffManager()
        cp = hm.create_checkpoint(
            checkpoint_id="cp-1",
            workflow_id="wf-1",
            step="codegen",
            state_snapshot={"files_modified": ["main.py"]},
            artifacts=["art-1"],
        )
        assert cp["checkpoint_id"] == "cp-1"
        assert cp["resumable"] is True

    def test_resume_from_checkpoint(self) -> None:
        hm = HandoffManager()
        hm.create_checkpoint("cp-1", "wf-1", "codegen", {"key": "value"})
        state = hm.resume_from("cp-1")
        assert state == {"key": "value"}

    def test_resume_missing_checkpoint(self) -> None:
        hm = HandoffManager()
        with pytest.raises(KeyError, match="not found"):
            hm.resume_from("nonexistent")

    def test_list_checkpoints(self) -> None:
        hm = HandoffManager()
        hm.create_checkpoint("cp-1", "wf-1", "step1", {})
        hm.create_checkpoint("cp-2", "wf-1", "step2", {})
        hm.create_checkpoint("cp-3", "wf-2", "step1", {})
        assert len(hm.list_checkpoints()) == 3
        assert len(hm.list_checkpoints(workflow_id="wf-1")) == 2

    def test_serialize_deserialize(self) -> None:
        hm = HandoffManager()
        hm.create_checkpoint("cp-1", "wf-1", "codegen", {"data": [1, 2, 3]})
        serialized = hm.serialize("cp-1")
        data = json.loads(serialized)
        assert data["checkpoint_id"] == "cp-1"

        hm2 = HandoffManager()
        restored = hm2.deserialize(serialized)
        assert restored["checkpoint_id"] == "cp-1"
        state = hm2.resume_from("cp-1")
        assert state == {"data": [1, 2, 3]}

    def test_handoff_log(self) -> None:
        hm = HandoffManager()
        hm.create_checkpoint("cp-1", "wf-1", "step1", {})
        hm.resume_from("cp-1")
        log = hm.handoff_log
        assert len(log) == 2
        assert log[0]["action"] == "checkpoint_created"
        assert log[1]["action"] == "resumed_from"

    def test_serialize_missing_raises(self) -> None:
        hm = HandoffManager()
        with pytest.raises(KeyError):
            hm.serialize("nonexistent")
