"""PlanExecute execution strategy — Phase 1: plan, Phase 2: execute steps."""

from __future__ import annotations

from typing import Any

from harness.core.modes.base import ExecutionStrategy, _resp_get
from harness.core.modes.react import _build_anthropic_tools


class PlanExecuteStrategy(ExecutionStrategy):
    """Two-phase execution: first generate a plan, then execute each step.

    Phase 1 (Plan): Ask LLM to produce a structured plan
    Phase 2 (Execute): Execute each plan step sequentially, using tools as needed
    """

    def __init__(self, max_plan_steps: int = 10, max_execute_steps_per_plan: int = 5, **kwargs: Any) -> None:
        self.max_plan_steps = max_plan_steps
        self.max_execute_steps_per_plan = max_execute_steps_per_plan

    @property
    def name(self) -> str:
        return "plan_execute"

    async def execute(
        self,
        messages: list[dict[str, Any]],
        provider: Any,
        tool_executor: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        output_schema = kwargs.get("output_schema")
        declared_tools: list[str] = kwargs.get("declared_tools", [])
        steps: list[dict[str, Any]] = []
        total_tokens = 0
        api_tools = _build_anthropic_tools(tool_executor, allowed=declared_tools or None)
        chat_kwargs: dict[str, Any] = {}
        if api_tools:
            chat_kwargs["tools"] = api_tools

        # Phase 1: Planning
        plan_messages = list(messages)
        plan_messages.append({
            "role": "user",
            "content": (
                "First, create a step-by-step plan to accomplish this task. "
                "Output your plan as a numbered list. Do not execute yet."
            ),
        })

        plan_response = await provider.chat(plan_messages)
        total_tokens += _resp_get(plan_response, "tokens_used", 0)
        plan_content = _resp_get(plan_response, "content", "")

        steps.append({
            "step": 1,
            "type": "plan",
            "content": plan_content,
        })

        # Phase 2: Execute each plan step
        execute_messages = list(messages)
        execute_messages.append({"role": "assistant", "content": plan_content})

        for step_num in range(self.max_plan_steps):
            execute_messages.append({
                "role": "user",
                "content": f"Now execute step {step_num + 1} of your plan. Use tools as needed.",
            })

            for _ in range(self.max_execute_steps_per_plan):
                response = await provider.chat(execute_messages, **chat_kwargs)
                total_tokens += _resp_get(response,"tokens_used", 0)
                tool_calls = _resp_get(response,"tool_calls", [])

                steps.append({
                    "step": step_num + 2,
                    "type": "execute",
                    "content": _resp_get(response,"content", ""),
                    "tool_calls": tool_calls,
                })

                if not tool_calls:
                    break

                # Build assistant message with tool_use blocks (Anthropic format)
                resp_content = _resp_get(response, "content", "")
                assistant_content: list[dict[str, Any]] = []
                if resp_content:
                    assistant_content.append({"type": "text", "text": resp_content})
                for tc in tool_calls:
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tc.get("id", f"tool_{step_num}"),
                        "name": tc.get("name", ""),
                        "input": tc.get("arguments", tc.get("input", {})),
                    })
                execute_messages.append({"role": "assistant", "content": assistant_content})

                # Execute tools and build tool_result message (Anthropic format)
                tool_results: list[dict[str, Any]] = []
                if tool_executor is not None:
                    for tc in tool_calls:
                        result = await tool_executor.execute(tc)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc.get("id", f"tool_{step_num}"),
                            "content": str(result.get("output", "")),
                        })
                execute_messages.append({"role": "user", "content": tool_results})

            # Check if LLM signals completion
            last_content = steps[-1].get("content", "")
            if any(marker in last_content.lower() for marker in ["plan complete", "all steps done", "task complete"]):
                break

        final_content = steps[-1].get("content", "") if steps else ""

        # If output_schema given, force a final structured-output summary call
        if output_schema:
            import json
            summary_msg = (
                "You have finished executing the plan. "
                "Now produce your final output as a JSON object matching this schema:\n\n"
                f"```json\n{json.dumps(output_schema, indent=2)}\n```\n\n"
                "Output JSON only — no prose, no markdown wrapper."
            )
            execute_messages.append({"role": "user", "content": summary_msg})
            try:
                final_response = await provider.chat(execute_messages, output_schema=output_schema)
                final_content = _resp_get(final_response, "content", final_content)
                total_tokens += _resp_get(final_response, "tokens_used", 0)
                steps.append({"step": len(steps) + 1, "type": "final_summary", "content": final_content})
            except Exception:
                pass

        return {
            "content": final_content,
            "tool_calls": [],
            "tokens_used": total_tokens,
            "steps": steps,
        }
