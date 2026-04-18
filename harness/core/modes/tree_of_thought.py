"""Tree-of-Thought execution strategy — branching deliberation with vote/pick.

The model generates multiple candidate thoughts (e.g. plans, approaches,
designs), then evaluates them and picks the best path. Simplified single-ply
variant — no multi-level tree expansion, just breadth at the root. Good for:

  * Branching planners that need to weigh alternatives before committing.
  * Multi-path deliberation / design exploration.
  * Architecture trade-off analysis.

Contract matches ReAct / ChainOfThought: returns
``{content, tool_calls, tokens_used, steps}`` where each step records one
candidate branch plus the final selection. Honours the caller's
``output_schema`` on the final pick turn so gate expressions can read
schema fields directly.
"""

from __future__ import annotations

import json
from typing import Any

from harness.core.modes.base import ExecutionStrategy, _resp_get


DEFAULT_BRANCHES = 3


class TreeOfThoughtStrategy(ExecutionStrategy):
    """Generate N candidates, have the model score them, return the best one."""

    def __init__(
        self,
        num_branches: int = DEFAULT_BRANCHES,
        selection: str = "vote",
        **_kwargs: Any,
    ) -> None:
        if num_branches < 2:
            raise ValueError("num_branches must be >= 2")
        if selection not in ("vote", "first", "longest"):
            raise ValueError(
                f"selection must be vote|first|longest, got {selection!r}"
            )
        self.num_branches = num_branches
        self.selection = selection

    @property
    def name(self) -> str:
        return "tree_of_thought"

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

        tot_system = (
            "You are running in tree-of-thought mode. Generate ONE distinct "
            "candidate solution per turn. Each candidate should take a "
            "different angle (pattern, trade-off, simplification) so the "
            "set covers a range of approaches. Do not compare candidates "
            "inside a single response."
        )
        branch_base = _prepend_system(messages, tot_system)

        candidates: list[str] = []
        for i in range(self.num_branches):
            branch_messages = list(branch_base)
            branch_messages.append({
                "role": "user",
                "content": (
                    f"Candidate {i + 1} of {self.num_branches}. Propose a "
                    "self-contained approach to the user's task in 3-6 "
                    "sentences. Do not reference other candidates."
                ),
            })
            response = await provider.chat(branch_messages)
            content = _resp_get(response, "content", "") or ""
            total_tokens += _resp_get(response, "tokens_used", 0) or 0
            candidates.append(content)
            steps.append({
                "step": i + 1,
                "type": "branch",
                "branch_index": i,
                "content": content,
            })

        # Selection phase
        if self.selection == "first":
            picked_idx, picked = 0, candidates[0]
            rationale = "first-branch selection (no LLM vote)"
        elif self.selection == "longest":
            picked_idx = max(range(len(candidates)), key=lambda i: len(candidates[i]))
            picked = candidates[picked_idx]
            rationale = "longest-branch selection (proxy for detail)"
        else:  # vote
            picked_idx, picked, rationale, vote_tokens = await _llm_vote(
                provider, messages, candidates
            )
            total_tokens += vote_tokens

        steps.append({
            "step": len(steps) + 1,
            "type": "select",
            "selection": self.selection,
            "picked_index": picked_idx,
            "rationale": rationale,
        })

        # Final composition — expand the picked candidate into the full answer,
        # enforcing output_schema if declared.
        final_messages = list(branch_base)
        final_messages.append({
            "role": "user",
            "content": (
                "Here is the chosen approach from our candidate pool:\n\n"
                f"{picked}\n\n"
                "Produce the FINAL answer to the original task based on this "
                "approach. Do not mention the candidate process."
            ),
        })
        if output_schema:
            final_messages[-1]["content"] += (
                "\n\nYour reply MUST be a single JSON object matching this schema:\n"
                f"```json\n{json.dumps(output_schema, indent=2)}\n```\n"
                "Emit raw JSON only."
            )

        final_kwargs: dict[str, Any] = {}
        if output_schema:
            final_kwargs["output_schema"] = output_schema
        final_response = await provider.chat(final_messages, **final_kwargs)
        final_content = _resp_get(final_response, "content", "") or ""
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


async def _llm_vote(
    provider: Any,
    original_messages: list[dict[str, Any]],
    candidates: list[str],
) -> tuple[int, str, str, int]:
    """Ask the model to pick the best candidate and return (idx, text, rationale, tokens)."""
    enumerated = "\n\n".join(
        f"## Candidate {i + 1}\n{c.strip()}"
        for i, c in enumerate(candidates)
    )
    vote_prompt = (
        "You have generated the following candidate approaches to a task:\n\n"
        f"{enumerated}\n\n"
        "Pick the single best candidate by correctness, simplicity, and "
        "coverage of the task requirements. Reply on two lines:\n"
        "  Line 1: PICK=<integer between 1 and "
        f"{len(candidates)}>\n"
        "  Line 2: REASON=<one short sentence>"
    )
    vote_messages = list(original_messages) + [
        {"role": "user", "content": vote_prompt}
    ]
    response = await provider.chat(vote_messages)
    content = _resp_get(response, "content", "") or ""
    tokens = _resp_get(response, "tokens_used", 0) or 0

    picked_idx = 0
    rationale = ""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("PICK="):
            try:
                raw = stripped.split("=", 1)[1].strip()
                n = int("".join(ch for ch in raw if ch.isdigit()) or "1")
                picked_idx = max(0, min(len(candidates) - 1, n - 1))
            except (ValueError, IndexError):
                picked_idx = 0
        elif stripped.upper().startswith("REASON="):
            rationale = stripped.split("=", 1)[1].strip()

    return picked_idx, candidates[picked_idx], rationale or "no rationale given", tokens
