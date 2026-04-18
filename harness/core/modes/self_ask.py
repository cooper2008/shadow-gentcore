"""SelfAsk execution strategy — decompose-question loop for multi-hop reasoning.

The agent is asked whether the current task needs clarifying sub-questions.
For each sub-question it answers in turn (optionally invoking tools), then
composes the final response from the accumulated Q/A pairs. Good for:

  * Ambiguous-spec analyzers that need to clarify intent before acting.
  * Multi-hop retrieval tasks (question → look up fact A → look up fact B
    → combine).
  * Reviewers that need to probe assumptions before approving.

Contract matches ReAct / ChainOfThought: returns
``{content, tool_calls, tokens_used, steps}`` where each step records the
sub-question and its answer. Honours the caller's ``output_schema``, falling
back to forced-JSON on the composition turn so gate expressions can read
schema fields directly.
"""

from __future__ import annotations

import json
import re
from typing import Any

from harness.core.modes.base import ExecutionStrategy, _resp_get


MAX_DEFAULT_ROUNDS = 4
STOP_MARKER = "NO_MORE_QUESTIONS"


class SelfAskStrategy(ExecutionStrategy):
    """Iteratively decompose the user task into sub-questions the model answers itself."""

    def __init__(self, max_rounds: int = MAX_DEFAULT_ROUNDS, **_kwargs: Any) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        self.max_rounds = max_rounds

    @property
    def name(self) -> str:
        return "self_ask"

    async def execute(
        self,
        messages: list[dict[str, Any]],
        provider: Any,
        tool_executor: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        output_schema = kwargs.get("output_schema")
        steps: list[dict[str, Any]] = []
        total_tokens = 0

        # Kick off: prepend a self-ask directive to the existing system prompt
        # so the agent knows the interaction style without the caller having
        # to author it. Kept idempotent — strategy-specific guidance only,
        # separate from output_schema hints that the provider injects.
        self_ask_system = (
            "You are running in self-ask mode. Before answering the user's "
            "task, decompose it into up to "
            f"{self.max_rounds} small sub-questions you can answer yourself. "
            "Emit one sub-question at a time on a line beginning with "
            "'Follow up:' and wait for the answer before continuing. When "
            f"you have enough information, write '{STOP_MARKER}' on its own "
            "line and produce the final answer."
        )
        working_messages = _prepend_system(messages, self_ask_system)

        for round_idx in range(self.max_rounds):
            response = await provider.chat(working_messages)
            content = _resp_get(response, "content", "")
            total_tokens += _resp_get(response, "tokens_used", 0) or 0
            working_messages.append({"role": "assistant", "content": content})

            question = _extract_followup(content)
            steps.append({
                "step": round_idx + 1,
                "type": "decompose",
                "content": content,
                "followup": question,
            })

            if question is None or STOP_MARKER in content:
                break

            # Answer the sub-question (no tools — just the model's own reasoning
            # for now; future extension can wire `tool_executor` here).
            working_messages.append({
                "role": "user",
                "content": (
                    f"Answer this sub-question as briefly as possible, then wait:\n"
                    f"{question}"
                ),
            })
            answer_response = await provider.chat(working_messages)
            answer_content = _resp_get(answer_response, "content", "")
            total_tokens += _resp_get(answer_response, "tokens_used", 0) or 0
            working_messages.append({"role": "assistant", "content": answer_content})
            steps.append({
                "step": round_idx + 1,
                "type": "answer",
                "question": question,
                "content": answer_content,
            })

        # Final composition turn — force schema-compliant JSON if one was declared.
        composition_prompt = (
            "Using the Q/A pairs above, produce the FINAL answer to the "
            "original user task."
        )
        if output_schema:
            composition_prompt += (
                "\n\nYour reply MUST be a single JSON object matching this schema:\n"
                f"```json\n{json.dumps(output_schema, indent=2)}\n```\n"
                "Emit raw JSON only."
            )
        working_messages.append({"role": "user", "content": composition_prompt})

        final_kwargs: dict[str, Any] = {}
        if output_schema:
            final_kwargs["output_schema"] = output_schema
        final_response = await provider.chat(working_messages, **final_kwargs)
        final_content = _resp_get(final_response, "content", "")
        total_tokens += _resp_get(final_response, "tokens_used", 0) or 0

        steps.append({
            "step": len(steps) + 1,
            "type": "compose",
            "content": final_content,
        })

        return {
            "content": final_content,
            "tool_calls": [],
            "tokens_used": total_tokens,
            "steps": steps,
        }


# ── helpers ───────────────────────────────────────────────────────────────

def _prepend_system(
    messages: list[dict[str, Any]],
    extra: str,
) -> list[dict[str, Any]]:
    """Return a new messages list with ``extra`` appended to the system prompt."""
    out = [dict(m) for m in messages]
    if out and out[0].get("role") == "system":
        out[0]["content"] = (out[0].get("content", "") or "") + "\n\n" + extra
    else:
        out.insert(0, {"role": "system", "content": extra})
    return out


def _extract_followup(content: str) -> str | None:
    """Pull the first 'Follow up: ...' line from a self-ask response, if any."""
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^\s*follow[\s-]*up\s*:\s*(.+?)\s*$", stripped, re.IGNORECASE)
        if match:
            question = match.group(1).strip()
            return question or None
    return None
