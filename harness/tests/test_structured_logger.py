"""Tests for structured JSON logger."""

from __future__ import annotations

import json

import pytest

from harness.core.metrics.logger import StructuredLogger


class TestStructuredLogger:
    def test_log_entry_structure(self) -> None:
        logger = StructuredLogger(name="test_logger")
        entry = logger.log("test_event", trace_id="t-1", agent_id="agent-1")
        assert entry["event"] == "test_event"
        assert entry["trace_id"] == "t-1"
        assert entry["agent_id"] == "agent-1"
        assert entry["level"] == "info"

    def test_log_extra_fields(self) -> None:
        logger = StructuredLogger(name="test_logger2")
        entry = logger.log("custom", tokens_used=500, custom_field="abc")
        assert entry["tokens_used"] == 500
        assert entry["custom_field"] == "abc"

    def test_log_run_start(self) -> None:
        logger = StructuredLogger(name="test_logger3")
        entry = logger.log_run_start(trace_id="t-1", agent_id="agent-1", model="claude-3")
        assert entry["event"] == "run_started"
        assert entry["model"] == "claude-3"

    def test_log_run_end(self) -> None:
        logger = StructuredLogger(name="test_logger4")
        entry = logger.log_run_end(
            trace_id="t-1", agent_id="agent-1", status="success",
            tokens_used=1000, cost_usd=0.1, duration_ms=500,
        )
        assert entry["event"] == "run_completed"
        assert entry["status"] == "success"
        assert entry["tokens_used"] == 1000
        assert entry["cost_usd"] == 0.1
        assert entry["duration_ms"] == 500

    def test_log_tool_call(self) -> None:
        logger = StructuredLogger(name="test_logger5")
        entry = logger.log_tool_call(
            trace_id="t-1", agent_id="agent-1",
            tool_name="pytest", success=True, duration_ms=200,
        )
        assert entry["event"] == "tool_called"
        assert entry["tool_name"] == "pytest"
        assert entry["success"] is True

    def test_entries_list(self) -> None:
        logger = StructuredLogger(name="test_logger6")
        logger.log("e1")
        logger.log("e2")
        logger.log("e3")
        assert len(logger.entries) == 3

    def test_clear(self) -> None:
        logger = StructuredLogger(name="test_logger7")
        logger.log("e1")
        logger.clear()
        assert len(logger.entries) == 0

    def test_json_serializable(self) -> None:
        logger = StructuredLogger(name="test_logger8")
        entry = logger.log("test", trace_id="t-1", extra={"nested": [1, 2]})
        json_str = json.dumps(entry, default=str)
        parsed = json.loads(json_str)
        assert parsed["event"] == "test"
