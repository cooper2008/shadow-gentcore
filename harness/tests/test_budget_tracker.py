"""Tests for BudgetTracker."""

from __future__ import annotations

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
