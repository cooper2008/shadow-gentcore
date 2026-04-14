"""Tests for BudgetTracker."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from harness.core.budget_tracker import BudgetTracker, BudgetExceededError


class TestBudgetTracker:
    def test_no_limits(self) -> None:
        bt = BudgetTracker()
        bt.record_usage(tokens=5000, cost_usd=0.05)
        assert bt.tokens_used == 5000
        assert bt.cost_usd == 0.05
        assert bt.tokens_remaining is None
        assert bt.cost_remaining is None

    def test_token_limit_ok(self) -> None:
        bt = BudgetTracker(max_tokens=10000)
        bt.record_usage(tokens=5000)
        assert bt.tokens_remaining == 5000
        assert bt.check_budget(estimated_tokens=4000)

    def test_token_limit_exceeded(self) -> None:
        bt = BudgetTracker(max_tokens=10000)
        bt.record_usage(tokens=8000)
        with pytest.raises(BudgetExceededError, match="Token budget exceeded"):
            bt.record_usage(tokens=3000)

    def test_cost_limit_exceeded(self) -> None:
        bt = BudgetTracker(max_cost_usd=1.0)
        bt.record_usage(tokens=5000, cost_usd=0.8)
        with pytest.raises(BudgetExceededError, match="Cost budget exceeded"):
            bt.record_usage(tokens=5000, cost_usd=0.3)

    def test_check_budget_preflight(self) -> None:
        bt = BudgetTracker(max_tokens=10000, max_cost_usd=1.0)
        bt.record_usage(tokens=8000, cost_usd=0.7)
        assert bt.check_budget(estimated_tokens=1000) is True
        assert bt.check_budget(estimated_tokens=3000) is False
        assert bt.check_budget(estimated_cost=0.2) is True
        assert bt.check_budget(estimated_cost=0.4) is False

    def test_call_count(self) -> None:
        bt = BudgetTracker()
        bt.record_usage(tokens=100)
        bt.record_usage(tokens=200)
        bt.record_usage(tokens=300)
        assert bt.call_count == 3

    def test_summary(self) -> None:
        bt = BudgetTracker(max_tokens=10000, max_cost_usd=1.0)
        bt.record_usage(tokens=3000, cost_usd=0.3)
        s = bt.summary()
        assert s["tokens_used"] == 3000
        assert s["tokens_limit"] == 10000
        assert s["tokens_remaining"] == 7000
        assert s["cost_usd"] == 0.3
        assert s["cost_limit"] == 1.0
        assert s["cost_remaining"] == pytest.approx(0.7)
        assert s["call_count"] == 1

    def test_reset(self) -> None:
        bt = BudgetTracker(max_tokens=10000)
        bt.record_usage(tokens=5000)
        bt.reset()
        assert bt.tokens_used == 0
        assert bt.cost_usd == 0.0
        assert bt.call_count == 0
        assert bt.tokens_remaining == 10000


class TestBudgetTrackerDuration:
    def test_duration_limit_not_exceeded(self) -> None:
        """No error when elapsed time is within the limit."""
        bt = BudgetTracker(max_duration_seconds=3600.0)
        # monotonic starts just after __init__, so elapsed is effectively 0
        bt.record_usage(tokens=100)  # must not raise

    def test_duration_limit_exceeded(self) -> None:
        """BudgetExceededError raised when elapsed time exceeds the limit."""
        start = 1000.0
        with patch("harness.core.budget_tracker.time") as mock_time:
            mock_time.monotonic.side_effect = [
                start,        # __init__ _start_time
                start + 3601, # _check_limits elapsed check
            ]
            bt = BudgetTracker(max_duration_seconds=3600.0)
            with pytest.raises(BudgetExceededError, match="Duration limit exceeded"):
                bt.record_usage(tokens=100)

    def test_no_duration_limit_no_check(self) -> None:
        """When max_duration_seconds is None, no duration check is performed."""
        bt = BudgetTracker()  # max_duration_seconds defaults to None
        # Simulate a very large elapsed time by overwriting _start_time
        bt._start_time = time.monotonic() - 999999
        bt.record_usage(tokens=100)  # must not raise

    def test_summary_includes_duration_fields(self) -> None:
        """summary() reports duration_seconds and duration_limit."""
        bt = BudgetTracker(max_duration_seconds=3600.0)
        bt.record_usage(tokens=500, cost_usd=0.05)
        s = bt.summary()
        assert "duration_seconds" in s
        assert "duration_limit" in s
        assert s["duration_limit"] == 3600.0
        assert isinstance(s["duration_seconds"], float)
        assert s["duration_seconds"] >= 0.0

    def test_summary_duration_limit_none_when_unset(self) -> None:
        """summary() duration_limit is None when no limit was configured."""
        bt = BudgetTracker()
        s = bt.summary()
        assert s["duration_limit"] is None
        assert isinstance(s["duration_seconds"], float)

    def test_reset_restarts_duration_clock(self) -> None:
        """reset() resets the start time so elapsed resets to near zero."""
        bt = BudgetTracker(max_duration_seconds=3600.0)
        # Push start_time far into the past so elapsed would be huge
        bt._start_time = time.monotonic() - 9999
        bt.reset()
        # After reset the clock is fresh; record_usage must not raise
        bt.record_usage(tokens=100)
