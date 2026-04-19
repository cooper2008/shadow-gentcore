"""CompositionEngine — executes WorkflowDefinition steps with DAG support."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from enum import Enum
from typing import Any

from harness.core.expr import evaluate as expr_evaluate
from harness.core.step_contract import StepArtifact, propagate_confidence
from harness.core.workflow_state import InMemoryStateStore, WorkflowStateStore

logger = logging.getLogger(__name__)


class ExecutionEvent(str, Enum):
    """Typed execution events — inspired by claw-code's lane events.

    Replaces string-based event names with typed enum for type safety.
    Since this extends str, existing tests comparing to strings still pass.
    """
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    GATE_PASSED = "gate_passed"
    GATE_FAILED = "gate_failed"
    GATE_RETRY = "gate_retry"
    GATE_RETRY_FRESH = "gate_retry_fresh"
    GATE_ROLLBACK = "gate_rollback"
    GATE_PASSED_ON_RETRY = "gate_passed_on_retry"
    GATE_PASSED_FRESH = "gate_passed_fresh"
    RETRY_EXHAUSTED = "retry_exhausted"
    FEEDBACK_LOOP_TRIGGERED = "feedback_loop_triggered"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    STATE_RESTORED = "state_restored_from_checkpoint"
    ERROR = "error"


def validate_reset_points(
    steps: list[dict[str, Any]],
    reset_points: list[str],
) -> None:
    """Validate that every gate's rollback target is a declared reset_point.

    Enforces the G-RST contract from FRAMEWORK_AUDIT_2026Q2: when a workflow
    declares `reset_points: [...]`, every step gate that uses ``on_fail:
    rollback`` or ``on_fail: fallback`` MUST point to one of those reset
    points via either ``fallback_step`` or ``rollback_to``.

    When ``reset_points`` is empty, the check is a no-op (backward compat).
    """
    if not reset_points:
        return

    allowed = set(reset_points)
    errors: list[str] = []

    for step in steps:
        gate = step.get("gate")
        if not gate:
            continue
        on_fail = gate.get("on_fail")
        if on_fail not in ("rollback", "fallback"):
            continue
        target = gate.get("rollback_to") or gate.get("fallback_step")
        if not target:
            continue
        if target not in allowed:
            errors.append(
                f"step {step.get('name', '?')!r} gate has "
                f"on_fail={on_fail!r} with fallback_step={target!r}, but "
                f"{target!r} is not in reset_points={sorted(allowed)}"
            )

    if errors:
        raise ValueError(
            "Workflow validation failed: "
            + "; ".join(errors)
            + ". Either add the missing step name to reset_points, or "
            "remove the invalid fallback."
        )


class GateFailure(Exception):
    """Raised when a workflow gate check fails and action is abort."""


class HumanEscalation(Exception):
    """Raised when a gate escalates to a human reviewer."""


class HumanApprovalRequired(Exception):
    """Raised when a workflow step requires human approval to continue.

    The workflow is paused — not failed.  The caller should persist this
    state and resume when ``/workflows/{workflow_id}/approve/{step_name}``
    is called.
    """

    def __init__(self, workflow_id: str, step_name: str, message: str = "") -> None:
        self.workflow_id = workflow_id
        self.step_name = step_name
        self.message = message
        super().__init__(
            f"Approval required for {workflow_id}/{step_name}: {message}"
        )


class CompositionEngine:
    """Executes a workflow by running steps in sequence.

    MVP: linear execution only (no DAG parallelism).
    Supports:
    - Sequential step execution via AgentRunner
    - Artifact passing between steps
    - Gate enforcement (retry, abort, escalate_human, fallback, degrade)
    - Gate retry with feedback injection
    - Cross-stage feedback loops
    """

    def __init__(
        self,
        agent_runner: Any = None,
        output_validator: Any = None,
        state_store: WorkflowStateStore | None = None,
    ) -> None:
        self._agent_runner = agent_runner
        self._output_validator = output_validator
        self._step_results: dict[str, dict[str, Any]] = {}
        self._execution_log: list[dict[str, Any]] = []
        self._feedback_loops: list[Any] = []
        self._state_store: WorkflowStateStore = state_store or InMemoryStateStore()
        # G7 — per-step observability. Aggregates retries, tokens, cost,
        # wall-clock duration per step name for CLI printers and reports.
        # Accessible via get_step_metrics(step_name) or get_step_metrics()
        # for the full map.
        self._step_metrics: dict[str, dict[str, Any]] = {}
        # Declared safe rollback targets; populated by the workflow loader.
        self._reset_points: list[str] = []

    def register_feedback_loop(self, loop: Any) -> None:
        """Register a FeedbackLoop for cross-stage feedback."""
        self._feedback_loops.append(loop)

    async def execute(
        self,
        steps: list[dict[str, Any]],
        step_configs: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Execute workflow steps in sequence.

        Args:
            steps: List of step dicts with 'name', 'agent', optional 'depends_on', 'gate'.
            step_configs: Per-step configuration (manifest, task, system_prompt, etc.).

        Returns:
            Dict with 'step_results', 'execution_log', 'status' keys.
        """
        step_configs = step_configs or {}

        for step in steps:
            step_name = step.get("name", "unnamed")
            agent_id = step.get("agent", "")
            gate = step.get("gate")

            self._execution_log.append({
                "event": ExecutionEvent.STEP_STARTED,
                "step": step_name,
                "agent": agent_id,
            })

            # Get step config or use defaults
            config = step_configs.get(step_name, {})

            # Gather inputs from dependencies
            depends_on = step.get("depends_on", [])
            dep_artifacts = {}
            for dep in depends_on:
                if dep in self._step_results:
                    dep_artifacts[dep] = self._step_results[dep]

            # Execute step
            try:
                result = await self._execute_step(step_name, agent_id, config, dep_artifacts)
                self._step_results[step_name] = result

                # Check gate
                if gate:
                    gate_passed = await self._check_gate(step_name, result, gate, config, step, workflow_id=None)
                    if not gate_passed:
                        return {
                            "step_results": dict(self._step_results),
                            "execution_log": list(self._execution_log),
                            "status": "gate_failed",
                            "failed_step": step_name,
                        }

                self._execution_log.append({
                    "event": ExecutionEvent.STEP_COMPLETED,
                    "step": step_name,
                    "status": "success",
                })

            except Exception as exc:
                self._execution_log.append({
                    "event": ExecutionEvent.STEP_FAILED,
                    "step": step_name,
                    "error": str(exc),
                })
                return {
                    "step_results": dict(self._step_results),
                    "execution_log": list(self._execution_log),
                    "status": "error",
                    "failed_step": step_name,
                    "error": str(exc),
                }

        return {
            "step_results": dict(self._step_results),
            "execution_log": list(self._execution_log),
            "status": "completed",
        }

    async def _execute_step(
        self,
        step_name: str,
        agent_id: str,
        config: dict[str, Any],
        dep_artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a single workflow step.

        Dependency outputs from prior steps are automatically injected as
        high-priority context items so the LLM always sees what earlier
        agents produced.
        """
        # G7 — per-step observability. Timing captures dep-context assembly,
        # LLM call, validator work, and gate checks. Metrics written here are
        # consumed by _run_dag_step's STEP_COMPLETED event and by any caller
        # using get_step_metrics() directly.
        _step_start_monotonic = time.monotonic()

        # Auto-inject dependency results into context so the agent sees them
        dep_context: list[dict[str, Any]] = []
        for dep_name, dep_result in dep_artifacts.items():
            output = dep_result.get("output") or dep_result.get("content") or ""
            if output:
                dep_context.append({
                    "source": f"prior_step:{dep_name}",
                    "content": f"### Output from step '{dep_name}':\n{output}",
                    "priority": 8,
                })

        if dep_context:
            existing = config.get("context_items") or []
            config = {**config, "context_items": existing + dep_context}

        if self._agent_runner is not None and "manifest" in config:
            manifest_data = config["manifest"]
            exec_mode = manifest_data.get("execution_mode", {})
            use_reflexion = exec_mode.get("reflection", False) if isinstance(exec_mode, dict) else getattr(exec_mode, "reflection", False)
            if use_reflexion and hasattr(self._agent_runner, "run_with_reflexion"):
                max_rounds = exec_mode.get("max_reflection_rounds", 3) if isinstance(exec_mode, dict) else getattr(exec_mode, "max_reflection_rounds", 3)
                threshold = exec_mode.get("reflection_threshold", 0.7) if isinstance(exec_mode, dict) else getattr(exec_mode, "reflection_threshold", 0.7)
                reflexion_out = await self._agent_runner.run_with_reflexion(
                    manifest=manifest_data,
                    task=config.get("task"),
                    system_prompt_content=config.get("system_prompt", ""),
                    context_items=config.get("context_items"),
                    max_reflexion_rounds=max_rounds,
                    reflexion_threshold=threshold,
                )
                result = reflexion_out.get("result", reflexion_out)
            else:
                result = await self._agent_runner.run(
                    manifest=manifest_data,
                    task=config.get("task"),
                    system_prompt_content=config.get("system_prompt", ""),
                    context_items=config.get("context_items"),
                )

            # Defensive: promote schema fields to top-level so validator + gate
            # expressions can read them. Prefer result["parsed_output"] (set by
            # AgentRunner's 4-strategy OutputParser) over raw content so we
            # handle prose+JSON, markdown fences, and multiple JSON blocks. Safe
            # to re-run — idempotent, skips already-present keys.
            if isinstance(result, dict):
                _inner = (
                    result.get("parsed_output")
                    if isinstance(result.get("parsed_output"), dict)
                    else (result.get("output") if isinstance(result.get("output"), dict) else None)
                )
                if _inner is None:
                    _content = result.get("content", "")
                    if isinstance(_content, str) and _content:
                        try:
                            _parsed = json.loads(_content)
                            if isinstance(_parsed, dict):
                                _inner = _parsed
                        except (json.JSONDecodeError, TypeError):
                            pass
                if isinstance(_inner, dict):
                    if not isinstance(result.get("output"), dict):
                        result["output"] = _inner
                    _reserved = {"result", "run_record", "budget_summary", "state_log",
                                 "status", "output", "content", "error", "_validation",
                                 "_artifact", "_confidence", "parsed_output",
                                 "output_parse_log"}
                    for _k, _v in _inner.items():
                        if _k not in _reserved and _k not in result:
                            result[_k] = _v

            # Post-execution: validate output against schema + grading criteria
            if self._output_validator is not None:
                agent_dir = config.get("agent_dir")
                provider = getattr(self._agent_runner, "provider", None)
                validation = await self._output_validator.validate(
                    output=result,
                    manifest=manifest_data,
                    agent_dir=agent_dir,
                    provider=provider,
                )
                result["_validation"] = validation
                if not validation["passed"]:
                    self._execution_log.append({
                        "event": ExecutionEvent.OUTPUT_VALIDATION_FAILED,
                        "step": step_name,
                        "score": validation["score"],
                        "issues": validation["issues"],
                    })

            # Ensure top-level status is set so gate conditions work
            if "status" not in result:
                result["status"] = "error" if result.get("error") else "completed"

            # Wrap result in typed artifact
            artifact = StepArtifact.from_result(step_name, result, agent_id=agent_id)

            # Propagate confidence from dependencies
            dep_arts = []
            for dep_name, dep_result in dep_artifacts.items():
                if isinstance(dep_result, dict) and "_artifact" in dep_result:
                    dep_arts.append(dep_result["_artifact"])
            if dep_arts:
                artifact.confidence = propagate_confidence(artifact, dep_arts)

            result["_artifact"] = artifact
            result["_confidence"] = artifact.confidence
            self._record_step_observability(step_name, result, _step_start_monotonic)
            return result
        # Stub: return a placeholder result
        stub_result = {
            "step": step_name,
            "agent": agent_id,
            "status": "completed",
            "output": config.get("mock_output", f"Output from {step_name}"),
            "dependencies": dep_artifacts,
        }
        self._record_step_observability(step_name, stub_result, _step_start_monotonic)
        return stub_result

    async def _check_gate(
        self,
        step_name: str,
        result: dict[str, Any],
        gate: dict[str, Any],
        config: dict[str, Any],
        step: dict[str, Any] | None = None,
        workflow_id: str | None = None,
    ) -> bool:
        """Check a gate condition after step execution.

        Recovery strategies (on_fail):
          retry         — inject feedback, keep context, try again (good for small fixes)
          retry_fresh   — CLEAR context, start from scratch (good when agent went wrong direction)
          rollback      — go back to a prior step's checkpoint, re-run from there
          abort         — stop the workflow immediately
          escalate_human — raise for human review
          degrade/fallback — continue despite failure

        Gate types:
          standard (default) — evaluates a boolean condition string
          router             — evaluates ordered routes, stores _routed_to in result
        """
        gate_name = gate.get("name", f"{step_name}_gate")
        on_fail = gate.get("on_fail", "abort")
        max_retries = gate.get("max_retries", 0)

        # ── ROUTER gate — dynamic routing based on output content ──
        gate_type = gate.get("type", "standard")
        if gate_type == "router":
            routes = gate.get("routes", [])
            for route in routes:
                if route.get("default"):
                    continue  # defer default routes to the end
                route_condition = route.get("condition", "")
                next_step = route.get("next_step", "")
                if self._evaluate_route_condition(route_condition, result) and next_step:
                    self._execution_log.append({
                        "event": ExecutionEvent.STEP_COMPLETED,
                        "step": step_name,
                        "routed_to": next_step,
                        "condition": route_condition,
                    })
                    result["_routed_to"] = next_step
                    return True
            # Check for default route
            default_route = next(
                (r.get("next_step") for r in routes if r.get("default")), None
            )
            if default_route:
                result["_routed_to"] = default_route
                return True
            # No route matched and no default — fail the gate
            logger.warning(
                "Router gate on step %r: no route matched and no default", step_name
            )
            return False

        # ── APPROVAL gate — pause workflow for human review ──────────────────
        if gate_type == "approval":
            approval_key = f"_approval_{step_name}"
            existing = self._state_store.load_step(workflow_id or "", approval_key)

            if existing is not None and existing.get("status") == "approved":
                # Human has already approved — pass the gate
                self._execution_log.append({
                    "event": ExecutionEvent.GATE_PASSED,
                    "gate": gate_name,
                    "step": step_name,
                    "approval": "approved",
                })
                return True

            if existing is None:
                # First encounter — create and persist the approval request
                approval: dict[str, Any] = {
                    "workflow_id": workflow_id or "",
                    "step_name": step_name,
                    "status": "pending",
                    "message": gate.get("message", "Human approval required"),
                    "result_preview": str(result.get("output", ""))[:500],
                    "created_at": time.time(),
                    "timeout_seconds": gate.get("timeout_seconds", 86400),
                }
                self._state_store.save_step(
                    workflow_id or "", approval_key, approval
                )

            # Raise so the caller knows the workflow is paused, not failed
            approval_data = existing or self._state_store.load_step(
                workflow_id or "", approval_key
            ) or {}
            raise HumanApprovalRequired(
                workflow_id=workflow_id or "",
                step_name=step_name,
                message=approval_data.get("message", "Human approval required"),
            )

        condition = gate.get("condition", "true")
        passed = self._evaluate_condition(condition, result)

        if passed:
            self._execution_log.append({
                "event": ExecutionEvent.GATE_PASSED,
                "gate": gate_name,
                "step": step_name,
            })
            return True

        # Gate failed — determine failure context. Coerce to str because
        # `output` may now be a dict (schema-promoted) rather than a string.
        _raw_output = result.get("output", result.get("content", ""))
        failed_output = str(_raw_output)[:500]
        validation = result.get("_validation", {})
        issues = validation.get("issues", [])
        score = validation.get("score", "N/A")

        gate_feedback = (
            f"Gate '{gate_name}' FAILED for step '{step_name}'.\n"
            f"Condition: {condition}\n"
            f"Score: {score}\n"
            f"Issues: {issues}\n"
            f"Failed output (truncated): {failed_output[:200]}"
        )

        self._execution_log.append({
            "event": ExecutionEvent.GATE_FAILED,
            "gate": gate_name,
            "step": step_name,
            "action": on_fail,
            "score": score,
            "issues": issues,
        })

        # Collect dependency artifacts for this step (needed for retries)
        dep_artifacts = {}
        if step:
            for dep in step.get("depends_on", []):
                if dep in self._step_results:
                    dep_artifacts[dep] = self._step_results[dep]

        # ── STRATEGY: retry (with feedback, keep context) ──
        if on_fail == "retry" and max_retries > 0:
            for attempt in range(1, max_retries + 1):
                self._bump_step_metric(step_name, "retry_count", 1)  # G7 observability
                self._execution_log.append({
                    "event": ExecutionEvent.GATE_RETRY,
                    "gate": gate_name,
                    "attempt": attempt,
                    "strategy": "retry_with_feedback",
                })
                retry_config = dict(config)
                # Inject feedback as a context item (not just a string)
                existing_ctx = config.get("context_items") or []
                retry_config["context_items"] = existing_ctx + [{
                    "source": f"gate_feedback:attempt_{attempt}",
                    "content": (
                        f"## Previous Attempt Failed (attempt {attempt})\n"
                        f"{gate_feedback}\n\n"
                        f"IMPORTANT: Take a DIFFERENT approach this time. "
                        f"Do not repeat the same mistake."
                    ),
                    "priority": 9,
                }]
                retry_config["retry_attempt"] = attempt
                retry_result = await self._execute_step(
                    step_name, result.get("agent", ""), retry_config, dep_artifacts,
                )
                self._step_results[step_name] = retry_result
                if self._evaluate_condition(condition, retry_result):
                    self._execution_log.append({
                        "event": ExecutionEvent.GATE_PASSED_ON_RETRY,
                        "gate": gate_name,
                        "attempt": attempt,
                    })
                    return True
            # All retries exhausted — fall through to retry_fresh or abort
            self._execution_log.append({
                "event": ExecutionEvent.RETRY_EXHAUSTED,
                "gate": gate_name,
                "attempts": max_retries,
            })
            return False

        # ── STRATEGY: retry_fresh (clear context, start from scratch) ──
        if on_fail == "retry_fresh" and max_retries > 0:
            for attempt in range(1, max_retries + 1):
                self._execution_log.append({
                    "event": ExecutionEvent.GATE_RETRY_FRESH,
                    "gate": gate_name,
                    "attempt": attempt,
                    "strategy": "fresh_start",
                })
                # Start with CLEAN config — only keep manifest, task, system_prompt
                fresh_config = {
                    "manifest": config.get("manifest"),
                    "task": config.get("task"),
                    "system_prompt": config.get("system_prompt", ""),
                    "context_items": (config.get("context_items") or [])[:],  # original context only
                }
                # Add a SHORT note about what went wrong (not the full failed output)
                fresh_config["context_items"].append({
                    "source": "recovery:fresh_start",
                    "content": (
                        f"## Fresh Start (previous {attempt} attempt(s) failed)\n"
                        f"Previous approach failed. Issues: {issues}\n"
                        f"Take a completely different approach this time."
                    ),
                    "priority": 9,
                })
                retry_result = await self._execute_step(
                    step_name, result.get("agent", ""), fresh_config, dep_artifacts,
                )
                self._step_results[step_name] = retry_result
                if self._evaluate_condition(condition, retry_result):
                    self._execution_log.append({
                        "event": ExecutionEvent.GATE_PASSED_FRESH,
                        "gate": gate_name,
                        "attempt": attempt,
                    })
                    return True
            return False

        # ── STRATEGY: rollback (re-run from a prior step) ──
        if on_fail == "rollback":
            rollback_to = gate.get("rollback_to") or gate.get("fallback_step")
            if rollback_to and rollback_to in self._step_results:
                is_reset_point = rollback_to in (self._reset_points or [])
                self._execution_log.append({
                    "event": ExecutionEvent.GATE_ROLLBACK,
                    "gate": gate_name,
                    "rollback_to": rollback_to,
                    "is_reset_point": is_reset_point,
                    "reason": f"Step '{step_name}' failed, rolling back to '{rollback_to}'",
                })
                # Clear results from rollback_to onwards
                steps_to_clear = []
                found = False
                for sr_name in list(self._step_results.keys()):
                    if sr_name == rollback_to:
                        found = True
                    if found:
                        steps_to_clear.append(sr_name)
                for sr_name in steps_to_clear:
                    del self._step_results[sr_name]
                # Note: the workflow will need to re-execute from rollback_to
                # This is handled by the caller detecting the rollback event
            return False

        if on_fail == "abort":
            raise GateFailure(f"Gate '{gate_name}' failed for step '{step_name}': abort")

        if on_fail == "escalate_human":
            raise HumanEscalation(
                f"Gate '{gate_name}' failed for step '{step_name}': escalated to human"
            )

        # degrade/fallback: allow workflow to continue
        return on_fail in ("degrade", "fallback")

    def _check_feedback_loops(
        self, step_name: str, result: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Check if any registered feedback loops should trigger for this step.

        Returns feedback dict if triggered, None otherwise.
        """
        for loop in self._feedback_loops:
            if loop.from_step == step_name and loop.should_trigger(result):
                feedback = {
                    "from_step": step_name,
                    "to_step": loop.to_step,
                    "result": result,
                    "message": f"Feedback from {step_name}: step did not succeed",
                }
                loop.record_iteration(feedback)
                self._execution_log.append({
                    "event": ExecutionEvent.FEEDBACK_LOOP_TRIGGERED,
                    "from_step": step_name,
                    "to_step": loop.to_step,
                    "iteration": loop.iterations_used,
                })
                return feedback
        return None

    def _evaluate_condition(self, condition: str, result: dict[str, Any]) -> bool:
        """Evaluate a gate condition against a step result.

        Delegates to harness.core.expr.evaluate (H2 — unified evaluator).
        Supports legacy aliases (`true`/`false`/`has_output`/`score >= N`/
        `<dotpath> is not empty`/`<field>_exists`) AND the full grammar
        (and/or/not, 6 comparators, dotpath, len(), is None / is empty,
        contains, in). Unparseable expressions log a warning and return
        False — fail-closed.

        Pre-H2 special: `status == success` and `status == completed` were
        treated as synonyms. Preserved here so existing workflows behave
        identically.
        """
        cond = condition.strip()
        if cond in ("status == success", "status == completed"):
            return result.get("status", "") in ("success", "completed")
        return expr_evaluate(condition, result)

    def _evaluate_route_condition(self, condition: str, result: dict[str, Any]) -> bool:
        """Evaluate a router condition against step result output.

        Recognized patterns:
          "output contains <keyword>"   — case-insensitive substring match on output
          "status == <value>"           — exact match on result status
          "confidence >= <N>"           — numeric threshold on _confidence/confidence
          "true" / "always"             — unconditionally True
        """
        output = str(result.get("output") or result.get("content") or "")
        cond = condition.strip().lower()

        # "output contains <keyword>"
        if cond.startswith("output contains "):
            keyword = condition.strip()[len("output contains "):].strip().strip("\"'")
            return keyword.lower() in output.lower()

        # "status == <value>"
        if cond.startswith("status == "):
            expected = condition.strip()[len("status == "):].strip().strip("\"'")
            return result.get("status", "") == expected

        # "confidence >= <N>"
        if cond.startswith("confidence >= "):
            try:
                threshold = float(condition.strip()[len("confidence >= "):])
                actual = float(result.get("_confidence", result.get("confidence", 0)))
                return actual >= threshold
            except (TypeError, ValueError):
                return False

        # "true" / "always"
        if cond in ("true", "always"):
            return True

        logger.warning("Unknown router condition: %r", condition)
        return False

    @property
    def step_results(self) -> dict[str, dict[str, Any]]:
        return dict(self._step_results)

    @property
    def execution_log(self) -> list[dict[str, Any]]:
        return list(self._execution_log)

    def reset(self) -> None:
        """Reset engine state for a new workflow execution."""
        self._step_results.clear()
        self._execution_log.clear()
        self._step_metrics.clear()

    # ── G7: per-step observability ───────────────────────────────────────

    def _record_step_observability(self, step_name: str, result: dict[str, Any], start_monotonic: float) -> None:
        """G7 — capture wall-clock + token + cost for a step after _execute_step runs.

        Called from _execute_step's return paths; surfaced on STEP_COMPLETED
        events and exposed via get_step_metrics(). Retry count is incremented
        separately by _check_gate's retry path.
        """
        duration_ms = int((time.monotonic() - start_monotonic) * 1000)
        self._bump_step_metric(step_name, "duration_ms", duration_ms)
        if isinstance(result, dict):
            tokens = int(result.get("tokens_used", 0) or 0)
            cost = float(result.get("cost_usd", 0.0) or 0.0)
            if tokens:
                self._bump_step_metric(step_name, "tokens_used", tokens)
            if cost:
                self._bump_step_metric(step_name, "cost_usd", cost)

    def _bump_step_metric(self, step_name: str, key: str, delta: Any = 1) -> None:
        """Increment (or overwrite, for non-numeric values) a metric for a step."""
        slot = self._step_metrics.setdefault(step_name, {
            "retry_count": 0,
            "tokens_used": 0,
            "cost_usd": 0.0,
            "duration_ms": 0,
        })
        if isinstance(delta, (int, float)) and isinstance(slot.get(key, 0), (int, float)):
            slot[key] = slot.get(key, 0) + delta
        else:
            slot[key] = delta

    def get_step_metrics(self, step_name: str | None = None) -> dict[str, Any]:
        """Return metrics for one step or all steps.

        Args:
            step_name: optional. When provided, returns the dict for that step
                (empty dict if the step hasn't run). When None, returns a copy
                of the full {step_name: metrics_dict} map.

        Returns:
            Per-step metrics with keys: retry_count, tokens_used, cost_usd,
            duration_ms. Missing keys default to 0.
        """
        if step_name is not None:
            return dict(self._step_metrics.get(step_name, {}))
        return {k: dict(v) for k, v in self._step_metrics.items()}

    # ── DAG Execution ─────────────────────────────────────────────────────

    @staticmethod
    def topological_sort(steps: list[dict[str, Any]]) -> list[list[str]]:
        """Topological sort of steps into execution layers.

        Returns a list of layers; steps within a layer can run in parallel.
        Raises ValueError on cyclic dependencies.
        """
        name_to_deps: dict[str, set[str]] = {}
        for step in steps:
            name = step.get("name", "")
            deps = set(step.get("depends_on", []))
            name_to_deps[name] = deps

        all_names = set(name_to_deps.keys())
        layers: list[list[str]] = []
        resolved: set[str] = set()

        while name_to_deps:
            layer = [n for n, deps in name_to_deps.items() if deps <= resolved]
            if not layer:
                remaining = list(name_to_deps.keys())
                raise ValueError(f"Cyclic dependency detected among: {remaining}")
            layers.append(sorted(layer))
            resolved.update(layer)
            for n in layer:
                del name_to_deps[n]

        return layers

    async def execute_dag(
        self,
        steps: list[dict[str, Any]],
        step_configs: dict[str, dict[str, Any]] | None = None,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute workflow steps as a DAG with parallel branches.

        Steps within the same topological layer run concurrently.

        Args:
            steps: Step definitions with optional ``depends_on`` edges.
            step_configs: Per-step configuration keyed by step name.
            workflow_id: Stable identifier for this workflow run.  Used as
                the key in the ``state_store`` so that a resumed run skips
                already-completed steps.  Defaults to
                ``"workflow-<epoch-ms>"`` (unique per invocation, meaning
                no resume unless the caller supplies a stable id).
        """
        step_configs = step_configs or {}
        step_map = {s["name"]: s for s in steps}

        if workflow_id is None:
            workflow_id = f"workflow-{int(time.time() * 1000)}"

        try:
            layers = self.topological_sort(steps)
        except ValueError as exc:
            return {
                "step_results": {},
                "execution_log": [{"event": ExecutionEvent.ERROR, "error": str(exc)}],
                "status": "error",
                "error": str(exc),
            }

        for layer in layers:
            tasks = []
            skipped: list[str] = []

            for step_name in layer:
                # ── Resume: skip steps already completed in a prior run ──
                cached = self._state_store.load_step(workflow_id, step_name)
                if cached is not None:
                    self._step_results[step_name] = cached
                    self._execution_log.append({
                        "event": ExecutionEvent.STATE_RESTORED,
                        "step": step_name,
                        "workflow_id": workflow_id,
                    })
                    logger.info("Resuming: skipping completed step %r", step_name)
                    skipped.append(step_name)
                    continue

                step = step_map[step_name]
                agent_id = step.get("agent", "")
                config = step_configs.get(step_name, {})
                depends_on = step.get("depends_on", [])
                dep_artifacts = {d: self._step_results[d] for d in depends_on if d in self._step_results}

                self._execution_log.append({
                    "event": ExecutionEvent.STEP_STARTED,
                    "step": step_name,
                    "agent": agent_id,
                    "layer": layers.index(layer),
                })

                tasks.append(
                    self._run_dag_step(
                        step_name, step, config, dep_artifacts,
                        step_configs, step_map, workflow_id,
                    )
                )

            if not tasks:
                # All steps in this layer were resumed from cache
                continue

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Map results back to the non-skipped step names in order
            non_skipped = [n for n in layer if n not in skipped]
            for step_name, result in zip(non_skipped, results):
                if isinstance(result, Exception):
                    # Approval gates pause the workflow — propagate as-is so
                    # the caller (server endpoint / CLI) can return 202.
                    if isinstance(result, HumanApprovalRequired):
                        raise result
                    self._execution_log.append({
                        "event": ExecutionEvent.STEP_FAILED,
                        "step": step_name,
                        "error": str(result),
                    })
                    return {
                        "step_results": dict(self._step_results),
                        "execution_log": list(self._execution_log),
                        "status": "error",
                        "failed_step": step_name,
                        "error": str(result),
                    }

        return {
            "step_results": dict(self._step_results),
            "execution_log": list(self._execution_log),
            "status": "completed",
        }

    async def _run_dag_step(
        self,
        step_name: str,
        step: dict[str, Any],
        config: dict[str, Any],
        dep_artifacts: dict[str, Any],
        step_configs: dict[str, dict[str, Any]] | None = None,
        step_map: dict[str, dict[str, Any]] | None = None,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a single DAG step and store its result.

        dep_artifacts are injected into context automatically by _execute_step.
        On success the result is persisted via the state_store so that a
        resumed run can skip this step.
        """
        step_configs = step_configs or {}
        step_map = step_map or {}
        agent_id = step.get("agent", "")
        # G7 — timing is handled inside _execute_step itself so direct
        # callers of that method also get metrics. This wrapper only
        # emits the STEP_COMPLETED event enriched from _step_metrics.
        result = await self._execute_step(step_name, agent_id, config, dep_artifacts)
        self._step_results[step_name] = result

        # Persist the completed result for durable resume
        if workflow_id is not None:
            self._state_store.save_step(workflow_id, step_name, result)

        # Check cross-stage feedback loops — re-execute target step if triggered
        feedback = self._check_feedback_loops(step_name, result)
        if feedback:
            to_step = feedback["to_step"]
            to_config = {**step_configs.get(to_step, {}), "feedback": feedback}
            to_step_def = step_map.get(to_step, {"name": to_step, "agent": ""})
            feedback_result = await self._execute_step(to_step, to_step_def.get("agent", ""), to_config, {})
            self._step_results[to_step] = feedback_result

        gate = step.get("gate")
        if gate:
            gate_passed = await self._check_gate(step_name, result, gate, config, step, workflow_id=workflow_id)
            if not gate_passed:
                raise GateFailure(f"Gate failed for {step_name}")

        # G7 — read metrics already written by _execute_step and surface
        # them on the STEP_COMPLETED event so downstream consumers (CLI
        # printers, RunRecord aggregators) get retries, tokens, cost, and
        # wall-clock duration per step without re-parsing logs.
        _metrics = self._step_metrics.get(step_name, {})
        self._execution_log.append({
            "event": ExecutionEvent.STEP_COMPLETED,
            "step": step_name,
            "status": "success",
            "retry_count": _metrics.get("retry_count", 0),
            "tokens_used": _metrics.get("tokens_used", 0),
            "cost_usd": _metrics.get("cost_usd", 0.0),
            "duration_ms": _metrics.get("duration_ms", 0),
        })
        return result

    # ── Reset Points ──────────────────────────────────────────────────────

    def checkpoint_state(self) -> dict[str, Any]:
        """Capture engine state at a reset point."""
        return {
            "step_results": dict(self._step_results),
            "execution_log": list(self._execution_log),
        }

    def restore_state(self, checkpoint: dict[str, Any]) -> None:
        """Restore engine state from a checkpoint (for reset points)."""
        self._step_results = dict(checkpoint.get("step_results", {}))
        self._execution_log = list(checkpoint.get("execution_log", []))
        self._execution_log.append({"event": ExecutionEvent.STATE_RESTORED})
