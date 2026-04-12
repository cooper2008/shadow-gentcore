"""AgentRunner — full agent execution pipeline from manifest to result."""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from agent_contracts.manifests.agent_manifest import AgentManifest
from agent_contracts.contracts.task_envelope import TaskEnvelope
from agent_contracts.contracts.run_record import RunRecord, RunStatus

from harness.core.prompt_assembler import PromptAssembler
from harness.core.mode_dispatcher import ModeDispatcher
from harness.core.tool_executor import ToolExecutor
from harness.core.budget_tracker import BudgetTracker, BudgetExceededError
from harness.core.output_parser import OutputParser


class AgentState(str, Enum):
    """Agent lifecycle states — inspired by claw-code's worker state machine.

    Provides observability into where an agent is in its execution pipeline.
    """
    SPAWNING = "spawning"
    READY = "ready"
    RUNNING = "running"
    TOOL_CALLING = "tool_calling"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentRunner:
    """Orchestrates a single agent execution from manifest loading to result.

    Pipeline:
    1. Load manifest and resolve system prompt
    2. Assemble prompt via PromptAssembler
    3. Select execution strategy via ModeDispatcher
    4. Execute strategy with budget tracking
    5. (Optional) Reflexion: grade output, re-run with critique if below threshold
    6. Return result and RunRecord
    """

    def __init__(
        self,
        provider: Any,
        prompt_assembler: PromptAssembler | None = None,
        mode_dispatcher: ModeDispatcher | None = None,
        tool_executor: ToolExecutor | None = None,
        grading_engine: Any | None = None,
    ) -> None:
        self.provider = provider
        self.prompt_assembler = prompt_assembler or PromptAssembler()
        self.mode_dispatcher = mode_dispatcher or ModeDispatcher()
        self.tool_executor = tool_executor
        self.grading_engine = grading_engine
        self._state_log: list[dict[str, Any]] = []

    def _set_state(self, state: AgentState, agent_id: str = "", detail: str = "") -> None:
        """Record a state transition."""
        self._state_log.append({
            "state": state.value,
            "agent_id": agent_id,
            "detail": detail,
            "timestamp": time.time(),
        })

    @property
    def state_log(self) -> list[dict[str, Any]]:
        """Return the state transition log for the last run."""
        return list(self._state_log)

    @staticmethod
    def _get(obj: Any, key: str, default: Any = None) -> Any:
        """Get a value from either a Pydantic model or a dict."""
        if hasattr(obj, key):
            return getattr(obj, key)
        if isinstance(obj, dict):
            return obj.get(key, default)
        return default

    async def run(
        self,
        manifest: Any,
        task: Any = None,
        system_prompt_content: str = "",
        tool_descriptions: list[dict[str, Any]] | None = None,
        context_items: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Execute an agent for a given task.

        Args:
            manifest: Agent manifest (AgentManifest or dict).
            task: Task envelope (TaskEnvelope or dict).
            system_prompt_content: Resolved system prompt text.
            tool_descriptions: Resolved tool descriptions for prompt assembly.
            context_items: Additional context items.

        Returns:
            Dict with 'result', 'run_record', 'budget_summary' keys.
        """
        task = task or {}
        self._state_log.clear()
        agent_id = self._get(manifest, "id", "unknown")
        self._set_state(AgentState.SPAWNING, agent_id)
        start_time = time.monotonic()
        task_id = self._get(task, "task_id", "unknown")
        trace_id = f"trace-{task_id}-{int(time.time())}"

        # Budget setup
        budget = BudgetTracker(
            max_tokens=self._get(task, "budget_tokens", 50000),
            max_cost_usd=self._get(task, "budget_cost_usd", 2.0),
        )

        # Extract output_schema and declared tool list from manifest.
        # Pass full dicts (preserving desc/level fields) so ToolDisclosureRouter
        # can partition L1 vs L2 tools.  String entries default to L2.
        output_schema: dict[str, Any] | None = None
        declared_tools: list[Any] = []
        if isinstance(manifest, dict):
            output_schema = manifest.get("output_schema")
            declared_tools = list(manifest.get("tools", []))
        else:
            # Pydantic AgentManifest — extract tool names from tool_bindings
            tool_bindings = getattr(manifest, "tool_bindings", None) or []
            declared_tools = [
                {"name": tb.name} if hasattr(tb, "name") else str(tb)
                for tb in tool_bindings
            ]

        # Assemble prompt
        messages = self.prompt_assembler.assemble(
            manifest=manifest,
            system_prompt_content=system_prompt_content,
            tool_descriptions=tool_descriptions,
            context_items=context_items,
            task_input=self._get(task, "input_payload", task if isinstance(task, dict) else {}),
            output_schema=output_schema,
        )

        # Select execution strategy
        execution_mode = self._get(task, "execution_mode_override")
        manifest_em = self._get(manifest, "execution_mode")
        if execution_mode is None and manifest_em:
            execution_mode = manifest_em.model_dump() if hasattr(manifest_em, "model_dump") else manifest_em
        strategy = self.mode_dispatcher.dispatch(execution_mode)
        self._set_state(AgentState.READY, agent_id)

        # Execute
        try:
            self._set_state(AgentState.RUNNING, agent_id)
            execute_kwargs: dict[str, Any] = {}
            if output_schema:
                execute_kwargs["output_schema"] = output_schema
            if declared_tools:
                execute_kwargs["declared_tools"] = declared_tools
            result = await strategy.execute(
                messages=messages,
                provider=self.provider,
                tool_executor=self.tool_executor,
                **execute_kwargs,
            )

            tokens_used = result.get("tokens_used", 0)
            budget.record_usage(tokens=tokens_used)

            # Post-process: extract structured JSON from content
            raw_content = result.get("content", "")
            parse_log: dict[str, Any] = {"attempted": False}
            if output_schema and raw_content:
                parse_log["attempted"] = True
                parsed = OutputParser().parse(raw_content, output_schema)
                if parsed is not None:
                    result["parsed_output"] = parsed
                    parse_log["success"] = True
                    parse_log["strategy"] = "output_parser"
                else:
                    parse_log["success"] = False
            result["output_parse_log"] = parse_log

            self._set_state(AgentState.VALIDATING, agent_id)

            duration_ms = int((time.monotonic() - start_time) * 1000)

            run_record = RunRecord(
                trace_id=trace_id,
                task_id=self._get(task, "task_id", "unknown"),
                workflow_id=self._get(task, "workflow_id"),
                agent_id=self._get(task, "agent_id", self._get(manifest, "id", "unknown")),
                agent_version=self._get(manifest, "version", "1.0.0"),
                provider="unknown",
                model="unknown",
                tokens_used=tokens_used,
                duration_ms=duration_ms,
                status=RunStatus.SUCCESS,
            )

            # Surface provider/API errors that were caught and stored in result
            result_error = result.get("error") if isinstance(result, dict) else None
            if result_error:
                self._set_state(AgentState.FAILED, agent_id, result_error)
                return {
                    "result": result,
                    "run_record": run_record,
                    "budget_summary": budget.summary(),
                    "state_log": list(self._state_log),
                    "status": "error",
                    "error": result_error,
                    "output": "",
                    "content": "",
                }

            self._set_state(AgentState.COMPLETED, agent_id)
            return {
                "result": result,
                "run_record": run_record,
                "budget_summary": budget.summary(),
                "state_log": list(self._state_log),
                "status": "completed",
                "output": result.get("content", "") if isinstance(result, dict) else str(result),
                "content": result.get("content", "") if isinstance(result, dict) else str(result),
            }

        except BudgetExceededError as exc:
            self._set_state(AgentState.FAILED, agent_id, str(exc))
            duration_ms = int((time.monotonic() - start_time) * 1000)
            run_record = RunRecord(
                trace_id=trace_id,
                task_id=self._get(task, "task_id", "unknown"),
                workflow_id=self._get(task, "workflow_id"),
                agent_id=self._get(task, "agent_id", self._get(manifest, "id", "unknown")),
                agent_version=self._get(manifest, "version", "1.0.0"),
                provider="unknown",
                model="unknown",
                tokens_used=budget.tokens_used,
                duration_ms=duration_ms,
                status=RunStatus.FAILURE,
                failure_reason=str(exc),
            )
            return {
                "result": {"content": "", "error": str(exc)},
                "run_record": run_record,
                "budget_summary": budget.summary(),
                "status": "error",
                "error": str(exc),
                "output": "",
                "content": "",
            }

        except Exception as exc:
            self._set_state(AgentState.FAILED, agent_id, str(exc))
            duration_ms = int((time.monotonic() - start_time) * 1000)
            run_record = RunRecord(
                trace_id=trace_id,
                task_id=self._get(task, "task_id", "unknown"),
                workflow_id=self._get(task, "workflow_id"),
                agent_id=self._get(task, "agent_id", self._get(manifest, "id", "unknown")),
                agent_version=self._get(manifest, "version", "1.0.0"),
                provider="unknown",
                model="unknown",
                tokens_used=budget.tokens_used,
                duration_ms=duration_ms,
                status=RunStatus.FAILURE,
                failure_reason=str(exc),
            )
            return {
                "result": {"content": "", "error": str(exc)},
                "run_record": run_record,
                "budget_summary": budget.summary(),
                "status": "error",
                "error": str(exc),
                "output": "",
                "content": "",
            }

    async def run_with_reflexion(
        self,
        manifest: AgentManifest,
        task: TaskEnvelope,
        system_prompt_content: str,
        contract: Any = None,
        tool_descriptions: list[dict[str, Any]] | None = None,
        context_items: list[dict[str, str]] | None = None,
        max_reflexion_rounds: int = 2,
        score_threshold: float = 1.0,
    ) -> dict[str, Any]:
        """Execute agent with self-critique / reflexion loop.

        After each execution, grades output via GradingEngine. If score is
        below threshold, re-runs with critique feedback injected into context.

        Args:
            manifest: Agent manifest.
            task: Task envelope.
            system_prompt_content: System prompt text.
            contract: FeatureContract for grading (optional).
            tool_descriptions: Tool descriptions.
            context_items: Context items.
            max_reflexion_rounds: Max re-run attempts.
            score_threshold: Score needed to pass without re-run.

        Returns:
            Dict with 'result', 'run_record', 'reflexion_history', 'rounds'.
        """
        reflexion_history: list[dict[str, Any]] = []
        current_context = list(context_items or [])
        last_result: dict[str, Any] = {}

        for round_num in range(1, max_reflexion_rounds + 1):
            last_result = await self.run(
                manifest=manifest,
                task=task,
                system_prompt_content=system_prompt_content,
                tool_descriptions=tool_descriptions,
                context_items=current_context,
            )

            reflexion_entry: dict[str, Any] = {
                "round": round_num,
                "result": last_result["result"],
            }

            # Grade if grading_engine and contract are available
            if self.grading_engine is not None and contract is not None:
                graded = await self.grading_engine.grade(
                    contract, last_result["result"], self.provider,
                )
                score = graded.score() if hasattr(graded, "score") else 0.0
                reflexion_entry["score"] = score
                reflexion_entry["graded"] = True

                if score >= score_threshold:
                    reflexion_entry["action"] = "pass"
                    reflexion_history.append(reflexion_entry)
                    break

                # Inject critique as context for next round
                manifest_schema: dict[str, Any] | None = None
                if isinstance(manifest, dict):
                    manifest_schema = manifest.get("output_schema")
                critique = self._build_critique(graded, output_schema=manifest_schema)
                current_context = list(context_items or []) + [
                    {"source": "reflexion_critique", "content": critique},
                ]
                reflexion_entry["action"] = "retry_with_critique"
                reflexion_entry["critique"] = critique
            else:
                reflexion_entry["graded"] = False
                reflexion_entry["action"] = "no_grading"
                reflexion_history.append(reflexion_entry)
                break

            reflexion_history.append(reflexion_entry)

        last_result["reflexion_history"] = reflexion_history
        last_result["rounds"] = len(reflexion_history)
        return last_result

    @staticmethod
    def _build_critique(graded_contract: Any, output_schema: dict[str, Any] | None = None) -> str:
        """Build a critique string from graded contract results."""
        import json
        lines = ["Previous attempt did not meet all criteria. Issues:"]
        for r in graded_contract.results:
            status = r.status.value if hasattr(r.status, "value") else str(r.status)
            if status != "PASS":
                lines.append(f"- {r.name}: {status} — {r.reason or 'no reason given'}")
        lines.append("\nPlease address these issues in your next attempt.")
        if output_schema:
            required = output_schema.get("required", [])
            lines.append("\nYour output MUST be valid JSON matching this schema:")
            lines.append(f"```json\n{json.dumps(output_schema, indent=2)}\n```")
            if required:
                lines.append(f"Required fields: {', '.join(required)}")
            # Build a minimal valid example
            props = output_schema.get("properties", {})
            example: dict[str, Any] = {}
            for field, prop in props.items():
                ftype = prop.get("type", "string")
                if ftype == "string":
                    example[field] = f"<{field}>"
                elif ftype in ("integer", "number"):
                    example[field] = 0
                elif ftype == "boolean":
                    example[field] = False
                elif ftype == "array":
                    example[field] = []
                elif ftype == "object":
                    example[field] = {}
            if example:
                lines.append(f"\nMinimal valid example:\n```json\n{json.dumps(example, indent=2)}\n```")
        return "\n".join(lines)
