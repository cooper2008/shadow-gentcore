"""ObservabilityBridge — reads logs, metrics, traces from local run records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ObservabilityBridge:
    """Reads observability data from local run record directories.

    Provides access to:
    - Run records (structured JSON logs)
    - Metrics (token usage, cost, duration)
    - Trace data from stored runs
    """

    def __init__(self, runs_dir: str | Path = ".harness/runs") -> None:
        self._runs_dir = Path(runs_dir)

    def list_traces(self) -> list[str]:
        """List all trace IDs from the runs directory."""
        if not self._runs_dir.exists():
            return []
        return [d.name for d in self._runs_dir.iterdir() if d.is_dir()]

    def get_run_record(self, trace_id: str) -> dict[str, Any] | None:
        """Read a run record by trace ID."""
        path = self._runs_dir / trace_id / "run_record.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def get_metrics(self, trace_id: str) -> dict[str, Any]:
        """Extract metrics from a run record."""
        record = self.get_run_record(trace_id)
        if record is None:
            return {}
        return {
            "tokens_used": record.get("tokens_used", 0),
            "cost_usd": record.get("cost_usd", 0.0),
            "duration_ms": record.get("duration_ms", 0),
            "status": record.get("status", "unknown"),
            "tool_usage": record.get("tool_usage", []),
        }

    def get_artifacts(self, trace_id: str) -> list[str]:
        """List artifact IDs for a trace."""
        art_dir = self._runs_dir / trace_id / "artifacts"
        if not art_dir.exists():
            return []
        return [p.stem for p in art_dir.glob("*.json")]

    def aggregate_metrics(self, trace_ids: list[str] | None = None) -> dict[str, Any]:
        """Aggregate metrics across multiple traces."""
        if trace_ids is None:
            trace_ids = self.list_traces()

        total_tokens = 0
        total_cost = 0.0
        total_duration = 0
        count = 0

        for tid in trace_ids:
            metrics = self.get_metrics(tid)
            if metrics:
                total_tokens += metrics.get("tokens_used", 0)
                total_cost += metrics.get("cost_usd", 0.0)
                total_duration += metrics.get("duration_ms", 0)
                count += 1

        return {
            "trace_count": count,
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost,
            "total_duration_ms": total_duration,
            "avg_tokens": total_tokens // count if count else 0,
            "avg_cost_usd": total_cost / count if count else 0.0,
        }
