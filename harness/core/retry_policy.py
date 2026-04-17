"""RetryPolicy — unified retry decision protocol (H7).

Before H7 the framework had FOUR independent retry mechanisms:
  1. AgentRunner.run_with_reflexion — same agent re-runs with self-critique
  2. CompositionEngine._check_gate (retry / retry_fresh / rollback) — gate retry
  3. FeedbackLoop — cross-step feedback loop
  4. EvaluatorLoop — planner/evaluator loop in _orchestrator/

Each had its own max-iterations knob, its own log-event name, and its own
notion of when-to-stop. H7 introduces a single `RetryPolicy` protocol so
all four mechanisms can delegate the decision to one place.

This file is **additive**. Existing mechanisms keep their public APIs
unchanged; they can *optionally* accept a `retry_policy` and ask it
whether to retry. Callers that don't pass one see zero behaviour change.

The unified log event name `RETRY_EVALUATED` carries `{mechanism,
attempt, max, decision, reason, cost_used_usd, tokens_used}` for
cross-mechanism debugging in the execution_log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# Unified log event name — consumers of execution_log can filter by this
# to see every retry decision across all four mechanisms.
RETRY_EVALUATED_EVENT = "retry_evaluated"


@dataclass
class RetryContext:
    """Per-attempt state passed to `should_retry`.

    Fields:
        mechanism: identifier of the retry surface ("gate", "reflexion",
            "feedback_loop", "evaluator_loop"). Populated by adapters.
        attempt: 1-indexed attempt number currently being evaluated. When
            the policy is asked after attempt N fails, this field is N.
        max_attempts: per-mechanism hint at the originally-configured cap.
            Policies may honour or override.
        cost_used_usd: running cost across attempts so far.
        tokens_used: running token count.
        last_error: optional exception from the last attempt.
        last_result: optional result dict from the last attempt (gates +
            reflexion use this to inspect score/status).
        extra: mechanism-specific payload (e.g. gate name, feedback score).
    """

    mechanism: str
    attempt: int = 0
    max_attempts: int = 3
    cost_used_usd: float = 0.0
    tokens_used: int = 0
    last_error: Exception | None = None
    last_result: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetryDecision:
    """Outcome of `should_retry`.

    Fields:
        retry: whether the mechanism should attempt again.
        reason: short human-readable explanation, surfaced in logs.
        delay_seconds: optional back-off before the next attempt. 0 = immediate.
        next_attempt_kwargs: extra payload the policy wants the next
            attempt to receive (e.g. fresh-context flag for retry_fresh).
    """

    retry: bool
    reason: str = ""
    delay_seconds: float = 0.0
    next_attempt_kwargs: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class RetryPolicy(Protocol):
    """Contract every retry surface can depend on.

    Implementations decide whether the caller should try again. A typical
    policy inspects `ctx.attempt` vs `ctx.max_attempts`, then checks
    `cost_used_usd` against a budget, then inspects the last error's
    class. Anything beyond that is policy-specific.
    """

    def should_retry(self, ctx: RetryContext) -> RetryDecision:
        """Return a RetryDecision for the current attempt."""
        ...

    def on_exhaust(self, ctx: RetryContext) -> None:
        """Called once after the final attempt, if retry was denied and
        max_attempts was reached. Policies can use this to emit a log
        event, increment a counter, or surface a ticket."""
        ...


# ── Standard policy ────────────────────────────────────────────────────────


class StandardRetryPolicy:
    """Default retry policy — max attempts + optional cost ceiling.

    Behaviour:
      * Retries up to `max_attempts` attempts. `attempt` is 1-indexed; a
        call with `attempt=1` after a failure asks: "should we do a 2nd
        attempt?" and so on.
      * Denies retry when `cost_used_usd` has reached `cost_budget_usd`
        (when set).
      * Denies retry for exceptions of any class in `non_retriable_errors`
        (default empty — retry all exceptions).
      * Uses `base_delay_seconds * (backoff_multiplier ** (attempt - 1))`
        as the back-off before the next attempt when `enable_backoff=True`.
        Default disabled — most in-process retries don't need sleep.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        cost_budget_usd: float | None = None,
        non_retriable_errors: tuple[type[BaseException], ...] = (),
        base_delay_seconds: float = 0.0,
        backoff_multiplier: float = 2.0,
        enable_backoff: bool = False,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.max_attempts = max_attempts
        self.cost_budget_usd = cost_budget_usd
        self.non_retriable_errors = non_retriable_errors
        self.base_delay_seconds = base_delay_seconds
        self.backoff_multiplier = backoff_multiplier
        self.enable_backoff = enable_backoff

    def should_retry(self, ctx: RetryContext) -> RetryDecision:
        # Per-mechanism cap takes precedence when lower than the policy's own.
        effective_max = min(self.max_attempts, ctx.max_attempts) if ctx.max_attempts else self.max_attempts

        if ctx.attempt >= effective_max:
            return RetryDecision(retry=False, reason=f"max_attempts={effective_max} reached")

        if self.cost_budget_usd is not None and ctx.cost_used_usd >= self.cost_budget_usd:
            return RetryDecision(
                retry=False,
                reason=f"cost_budget_usd={self.cost_budget_usd} exhausted (used {ctx.cost_used_usd:.3f})",
            )

        if ctx.last_error is not None and isinstance(ctx.last_error, self.non_retriable_errors):
            return RetryDecision(
                retry=False,
                reason=f"non-retriable error: {type(ctx.last_error).__name__}",
            )

        delay = self._compute_delay(ctx.attempt) if self.enable_backoff else 0.0
        return RetryDecision(
            retry=True,
            reason=f"retrying (attempt {ctx.attempt + 1}/{effective_max})",
            delay_seconds=delay,
        )

    def on_exhaust(self, ctx: RetryContext) -> None:
        logger.info(
            "Retry exhausted mechanism=%s attempts=%s cost_used_usd=%s last_error=%s",
            ctx.mechanism,
            ctx.attempt,
            ctx.cost_used_usd,
            type(ctx.last_error).__name__ if ctx.last_error else None,
        )

    def _compute_delay(self, attempt: int) -> float:
        """Exponential back-off: base * multiplier^(attempt-1)."""
        return self.base_delay_seconds * (self.backoff_multiplier ** max(attempt - 1, 0))


# ── No-retry policy (opt-out for tests + deterministic runs) ───────────────


class NoRetryPolicy:
    """Policy that always denies retries — useful for tests + dry runs."""

    def should_retry(self, ctx: RetryContext) -> RetryDecision:
        return RetryDecision(retry=False, reason="NoRetryPolicy: retries disabled")

    def on_exhaust(self, ctx: RetryContext) -> None:
        return None


# ── Adapter helpers ────────────────────────────────────────────────────────


def evaluate(policy: RetryPolicy, ctx: RetryContext) -> RetryDecision:
    """Convenience wrapper — calls `policy.should_retry(ctx)`.

    Adapters on existing mechanisms (composition_engine, agent_runner,
    feedback_loop, evaluator_loop) import + call this single function so
    swapping policies is one-line at the call site.
    """
    return policy.should_retry(ctx)


def build_retry_log_event(
    mechanism: str,
    ctx: RetryContext,
    decision: RetryDecision,
) -> dict[str, Any]:
    """Produce a unified log event payload — one format across all 4 mechanisms.

    Consumers of execution_log can filter by `event == RETRY_EVALUATED_EVENT`
    and get retry context without knowing which mechanism emitted it.
    """
    return {
        "event": RETRY_EVALUATED_EVENT,
        "mechanism": mechanism,
        "attempt": ctx.attempt,
        "max_attempts": ctx.max_attempts,
        "cost_used_usd": ctx.cost_used_usd,
        "tokens_used": ctx.tokens_used,
        "retry": decision.retry,
        "reason": decision.reason,
        "delay_seconds": decision.delay_seconds,
    }
