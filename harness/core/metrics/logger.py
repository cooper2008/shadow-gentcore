"""Structured JSON logging with trace_id, agent_id, token/cost/duration per run."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


class StructuredLogger:
    """JSON-structured logger for agent execution.

    Emits log entries as JSON with consistent fields:
    - trace_id, agent_id, event, timestamp
    - Token usage, cost, duration per run
    """

    def __init__(self, name: str = "harness", level: int = logging.INFO) -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)
        self._entries: list[dict[str, Any]] = []

        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)

    def log(
        self,
        event: str,
        trace_id: str = "",
        agent_id: str = "",
        level: str = "info",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Emit a structured log entry.

        Args:
            event: Event name (e.g., 'run_started', 'tool_called', 'run_completed').
            trace_id: Trace ID for correlation.
            agent_id: Agent ID for attribution.
            level: Log level ('debug', 'info', 'warning', 'error').
            **kwargs: Additional fields (tokens_used, cost_usd, duration_ms, etc.).

        Returns:
            The log entry dict.
        """
        entry: dict[str, Any] = {
            "event": event,
            "trace_id": trace_id,
            "agent_id": agent_id,
            "level": level,
        }
        entry.update(kwargs)
        self._entries.append(entry)

        json_str = json.dumps(entry, default=str)
        log_fn = getattr(self._logger, level, self._logger.info)
        log_fn(json_str)

        return entry

    def log_run_start(self, trace_id: str, agent_id: str, **kwargs: Any) -> dict[str, Any]:
        """Log the start of an agent run."""
        return self.log("run_started", trace_id=trace_id, agent_id=agent_id, **kwargs)

    def log_run_end(
        self,
        trace_id: str,
        agent_id: str,
        status: str,
        tokens_used: int = 0,
        cost_usd: float = 0.0,
        duration_ms: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Log the end of an agent run with metrics."""
        return self.log(
            "run_completed",
            trace_id=trace_id,
            agent_id=agent_id,
            status=status,
            tokens_used=tokens_used,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            **kwargs,
        )

    def log_tool_call(
        self,
        trace_id: str,
        agent_id: str,
        tool_name: str,
        success: bool,
        duration_ms: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Log a tool invocation."""
        return self.log(
            "tool_called",
            trace_id=trace_id,
            agent_id=agent_id,
            tool_name=tool_name,
            success=success,
            duration_ms=duration_ms,
            **kwargs,
        )

    @property
    def entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries.clear()
