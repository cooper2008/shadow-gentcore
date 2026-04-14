"""Typed artifacts for inter-agent handoffs in DAG workflows."""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field


class StepArtifact(BaseModel):
    """Typed wrapper for DAG step outputs — replaces raw dicts."""

    step_name: str
    agent_id: str = ""
    status: str = "completed"
    output: Any = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    schema_version: str = "1.0"
    timestamp: float = Field(default_factory=time.time)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_result(
        cls, step_name: str, result: dict[str, Any], agent_id: str = ""
    ) -> "StepArtifact":
        """Create a StepArtifact from a raw step result dict."""
        # Extract confidence from validation if available
        validation = result.get("_validation", {})
        confidence = validation.get("score", 1.0) if validation else 1.0
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 1.0
        confidence = max(0.0, min(1.0, confidence))

        return cls(
            step_name=step_name,
            agent_id=agent_id or result.get("agent_id", ""),
            status=result.get("status", "completed"),
            output=result.get("output") or result.get("content") or result.get("result"),
            confidence=confidence,
            metadata={
                k: v
                for k, v in result.items()
                if k not in ("output", "content", "result", "status", "_validation", "agent_id")
            },
        )


def propagate_confidence(
    artifact: StepArtifact, dependency_artifacts: list[StepArtifact]
) -> float:
    """Calculate propagated confidence: min(own, all_dependencies).

    The pipeline is only as confident as its weakest link — if any upstream
    step produced a low-confidence result, that uncertainty flows downstream.

    Args:
        artifact: The current step's artifact with its own confidence score.
        dependency_artifacts: Typed artifacts from all dependency steps.

    Returns:
        Propagated confidence clamped to [0.0, 1.0].
    """
    if not dependency_artifacts:
        return artifact.confidence
    min_dep = min(d.confidence for d in dependency_artifacts)
    return min(artifact.confidence, min_dep)
