"""HandoffManager — checkpoint at reset points, resume from checkpoint."""

from __future__ import annotations

import json
from typing import Any


class HandoffManager:
    """Manages workflow handoffs between agent steps via checkpoints.

    Responsibilities:
    - Create checkpoints at reset points
    - Resume execution from a checkpoint
    - Track handoff history
    """

    def __init__(self) -> None:
        self._checkpoints: dict[str, dict[str, Any]] = {}
        self._handoff_log: list[dict[str, Any]] = []

    def create_checkpoint(
        self,
        checkpoint_id: str,
        workflow_id: str,
        step: str,
        state_snapshot: dict[str, Any],
        artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a checkpoint at a reset point.

        Args:
            checkpoint_id: Unique checkpoint identifier.
            workflow_id: The workflow this checkpoint belongs to.
            step: The step name at which the checkpoint is taken.
            state_snapshot: Serializable state to preserve.
            artifacts: List of artifact IDs produced so far.

        Returns:
            The checkpoint dict.
        """
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "workflow_id": workflow_id,
            "step": step,
            "state_snapshot": state_snapshot,
            "artifacts": artifacts or [],
            "resumable": True,
        }
        self._checkpoints[checkpoint_id] = checkpoint
        self._handoff_log.append({
            "action": "checkpoint_created",
            "checkpoint_id": checkpoint_id,
            "step": step,
        })
        return checkpoint

    def resume_from(self, checkpoint_id: str) -> dict[str, Any]:
        """Resume execution from a checkpoint.

        Args:
            checkpoint_id: The checkpoint to resume from.

        Returns:
            The checkpoint state snapshot.

        Raises:
            KeyError: If checkpoint not found.
            ValueError: If checkpoint is not resumable.
        """
        checkpoint = self._checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise KeyError(f"Checkpoint '{checkpoint_id}' not found")
        if not checkpoint.get("resumable", False):
            raise ValueError(f"Checkpoint '{checkpoint_id}' is not resumable")

        self._handoff_log.append({
            "action": "resumed_from",
            "checkpoint_id": checkpoint_id,
            "step": checkpoint["step"],
        })
        return checkpoint["state_snapshot"]

    def list_checkpoints(self, workflow_id: str | None = None) -> list[dict[str, Any]]:
        """List all checkpoints, optionally filtered by workflow."""
        if workflow_id is None:
            return list(self._checkpoints.values())
        return [
            cp for cp in self._checkpoints.values()
            if cp["workflow_id"] == workflow_id
        ]

    def serialize(self, checkpoint_id: str) -> str:
        """Serialize a checkpoint to JSON string."""
        checkpoint = self._checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise KeyError(f"Checkpoint '{checkpoint_id}' not found")
        return json.dumps(checkpoint, default=str)

    def deserialize(self, data: str) -> dict[str, Any]:
        """Deserialize a checkpoint from JSON string and register it."""
        checkpoint = json.loads(data)
        cp_id = checkpoint["checkpoint_id"]
        self._checkpoints[cp_id] = checkpoint
        return checkpoint

    @property
    def handoff_log(self) -> list[dict[str, Any]]:
        return list(self._handoff_log)
