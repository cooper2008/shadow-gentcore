"""Tests for H7 — RetryPolicy protocol + adapters."""

from __future__ import annotations


import pytest

from harness.core.retry_policy import (
    NoRetryPolicy,
    RETRY_EVALUATED_EVENT,
    RetryContext,
    RetryDecision,
    RetryPolicy,
    StandardRetryPolicy,
    build_retry_log_event,
    evaluate,
)


# ── Protocol conformance ───────────────────────────────────────────────────


class TestProtocolConformance:
    def test_standard_policy_implements_protocol(self) -> None:
        policy: RetryPolicy = StandardRetryPolicy()
        assert isinstance(policy, RetryPolicy)

    def test_no_retry_policy_implements_protocol(self) -> None:
        policy: RetryPolicy = NoRetryPolicy()
        assert isinstance(policy, RetryPolicy)

    def test_duck_typed_policy_implements_protocol(self) -> None:
        """Any object with should_retry + on_exhaust satisfies Protocol."""

        class DuckPolicy:
            def should_retry(self, ctx: RetryContext) -> RetryDecision:
                return RetryDecision(retry=True, reason="duck")

            def on_exhaust(self, ctx: RetryContext) -> None:
                return None

        assert isinstance(DuckPolicy(), RetryPolicy)


# ── StandardRetryPolicy: max attempts ──────────────────────────────────────


class TestMaxAttempts:
    def test_first_attempt_retries(self) -> None:
        policy = StandardRetryPolicy(max_attempts=3)
        ctx = RetryContext(mechanism="gate", attempt=1, max_attempts=3)
        decision = policy.should_retry(ctx)
        assert decision.retry is True
        assert "attempt 2/3" in decision.reason

    def test_retry_denied_at_cap(self) -> None:
        policy = StandardRetryPolicy(max_attempts=3)
        ctx = RetryContext(mechanism="gate", attempt=3, max_attempts=3)
        decision = policy.should_retry(ctx)
        assert decision.retry is False
        assert "max_attempts" in decision.reason

    def test_per_mechanism_cap_takes_precedence_when_lower(self) -> None:
        """Policy allows 5 attempts; mechanism only wants 2 — honour the 2."""
        policy = StandardRetryPolicy(max_attempts=5)
        ctx = RetryContext(mechanism="gate", attempt=2, max_attempts=2)
        decision = policy.should_retry(ctx)
        assert decision.retry is False
        assert "max_attempts=2" in decision.reason

    def test_policy_rejects_invalid_max_attempts(self) -> None:
        with pytest.raises(ValueError):
            StandardRetryPolicy(max_attempts=0)


# ── StandardRetryPolicy: cost budget ───────────────────────────────────────


class TestCostBudget:
    def test_retry_denied_when_over_budget(self) -> None:
        policy = StandardRetryPolicy(max_attempts=10, cost_budget_usd=1.0)
        ctx = RetryContext(mechanism="reflexion", attempt=2, cost_used_usd=1.5)
        decision = policy.should_retry(ctx)
        assert decision.retry is False
        assert "cost_budget_usd" in decision.reason

    def test_retry_allowed_under_budget(self) -> None:
        policy = StandardRetryPolicy(max_attempts=10, cost_budget_usd=1.0)
        ctx = RetryContext(mechanism="reflexion", attempt=1, cost_used_usd=0.4)
        decision = policy.should_retry(ctx)
        assert decision.retry is True

    def test_no_budget_means_no_cost_check(self) -> None:
        policy = StandardRetryPolicy(max_attempts=10)  # cost_budget_usd=None
        ctx = RetryContext(mechanism="reflexion", attempt=1, cost_used_usd=99999.0)
        assert policy.should_retry(ctx).retry is True


# ── StandardRetryPolicy: non-retriable exceptions ─────────────────────────


class TestNonRetriableErrors:
    def test_retry_denied_for_non_retriable_error(self) -> None:
        class AuthError(Exception):
            pass

        policy = StandardRetryPolicy(
            max_attempts=5, non_retriable_errors=(AuthError,)
        )
        ctx = RetryContext(mechanism="feedback_loop", attempt=1, last_error=AuthError("403"))
        decision = policy.should_retry(ctx)
        assert decision.retry is False
        assert "AuthError" in decision.reason

    def test_retry_allowed_for_other_errors(self) -> None:
        class AuthError(Exception):
            pass

        policy = StandardRetryPolicy(
            max_attempts=5, non_retriable_errors=(AuthError,)
        )
        ctx = RetryContext(
            mechanism="feedback_loop",
            attempt=1,
            last_error=RuntimeError("timeout"),
        )
        assert policy.should_retry(ctx).retry is True


# ── Back-off computation ──────────────────────────────────────────────────


class TestBackoff:
    def test_no_backoff_by_default(self) -> None:
        policy = StandardRetryPolicy(max_attempts=5, base_delay_seconds=1.0)
        # enable_backoff=False by default
        ctx = RetryContext(mechanism="gate", attempt=1)
        assert policy.should_retry(ctx).delay_seconds == 0.0

    def test_exponential_backoff_when_enabled(self) -> None:
        policy = StandardRetryPolicy(
            max_attempts=5,
            base_delay_seconds=1.0,
            backoff_multiplier=2.0,
            enable_backoff=True,
        )
        # max_attempts=5 on both policy + ctx so attempts below 5 still retry
        # Attempt 1 → delay = 1 * 2^0 = 1.0
        assert policy.should_retry(RetryContext(mechanism="gate", attempt=1, max_attempts=5)).delay_seconds == 1.0
        # Attempt 2 → delay = 1 * 2^1 = 2.0
        assert policy.should_retry(RetryContext(mechanism="gate", attempt=2, max_attempts=5)).delay_seconds == 2.0
        # Attempt 3 → delay = 1 * 2^2 = 4.0
        assert policy.should_retry(RetryContext(mechanism="gate", attempt=3, max_attempts=5)).delay_seconds == 4.0


# ── NoRetryPolicy ──────────────────────────────────────────────────────────


class TestNoRetryPolicy:
    def test_always_denies(self) -> None:
        policy = NoRetryPolicy()
        for attempt in (1, 5, 10):
            ctx = RetryContext(mechanism="gate", attempt=attempt, max_attempts=10)
            decision = policy.should_retry(ctx)
            assert decision.retry is False
            assert "NoRetryPolicy" in decision.reason

    def test_on_exhaust_is_noop(self) -> None:
        policy = NoRetryPolicy()
        ctx = RetryContext(mechanism="gate", attempt=1)
        assert policy.on_exhaust(ctx) is None


# ── evaluate() convenience wrapper ─────────────────────────────────────────


class TestEvaluateWrapper:
    def test_evaluate_forwards_to_policy(self) -> None:
        policy = StandardRetryPolicy(max_attempts=2)
        ctx = RetryContext(mechanism="gate", attempt=1, max_attempts=2)
        direct = policy.should_retry(ctx)
        via_wrapper = evaluate(policy, ctx)
        assert direct == via_wrapper


# ── Unified log event ──────────────────────────────────────────────────────


class TestLogEvent:
    def test_event_name_constant(self) -> None:
        assert RETRY_EVALUATED_EVENT == "retry_evaluated"

    def test_log_event_shape(self) -> None:
        ctx = RetryContext(
            mechanism="gate",
            attempt=2,
            max_attempts=5,
            cost_used_usd=0.12,
            tokens_used=1200,
        )
        decision = RetryDecision(retry=True, reason="retry 3/5", delay_seconds=0.5)
        event = build_retry_log_event("gate", ctx, decision)

        assert event["event"] == RETRY_EVALUATED_EVENT
        assert event["mechanism"] == "gate"
        assert event["attempt"] == 2
        assert event["max_attempts"] == 5
        assert event["retry"] is True
        assert event["reason"] == "retry 3/5"
        assert event["cost_used_usd"] == 0.12
        assert event["tokens_used"] == 1200
        assert event["delay_seconds"] == 0.5

    def test_event_uniform_across_mechanisms(self) -> None:
        """All four mechanisms use the same event name + payload shape."""
        ctx = RetryContext(mechanism="x", attempt=1, max_attempts=1)
        decision = RetryDecision(retry=False, reason="cap")
        for mechanism in ("gate", "reflexion", "feedback_loop", "evaluator_loop"):
            event = build_retry_log_event(mechanism, ctx, decision)
            assert set(event.keys()) == {
                "event", "mechanism", "attempt", "max_attempts",
                "cost_used_usd", "tokens_used", "retry", "reason", "delay_seconds",
            }


# ── Integration demo: the same policy plugs into any mechanism ────────────


class TestIntegrationDemo:
    """Prove the protocol IS the integration surface — no adapters needed
    to share a policy across mechanisms."""

    def test_single_policy_handles_all_four_mechanisms(self) -> None:
        policy = StandardRetryPolicy(max_attempts=2, cost_budget_usd=0.50)

        decisions = {}
        for mechanism in ("gate", "reflexion", "feedback_loop", "evaluator_loop"):
            # Each mechanism asks the policy the same question.
            ctx = RetryContext(mechanism=mechanism, attempt=1, max_attempts=2, cost_used_usd=0.10)
            decisions[mechanism] = policy.should_retry(ctx)

        # All four get the same decision because the policy doesn't know
        # or care which mechanism is asking.
        assert all(d.retry for d in decisions.values())

        # Exhaust cost — all four must now deny.
        for mechanism in ("gate", "reflexion", "feedback_loop", "evaluator_loop"):
            ctx = RetryContext(mechanism=mechanism, attempt=1, max_attempts=2, cost_used_usd=1.0)
            assert policy.should_retry(ctx).retry is False
