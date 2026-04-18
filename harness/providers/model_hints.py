"""Model-specific prompt nudges.

Some models need a little extra help to produce schema-compliant output
after multi-turn tool loops. This module maps model-id patterns → short
system-prompt suffixes. Hints are only applied when the model ID matches;
strong models (Claude Opus/Sonnet, GPT-5+) receive no suffix so their
prompts stay lean.

Kept deliberately narrow — one or two lines per model. The goal is to
unblock known weaknesses, not to re-specify the task.
"""

from __future__ import annotations


# Observed weakness: after completing all tool calls, emits prose describing
# what it did instead of the required JSON object. Also occasionally
# hallucinates Alembic APIs (e.g. `op.column` instead of `sa.Column`) when
# writing migrations from memory.
MINIMAX_HINT = (
    "\n\n--- Model-specific guidance ---\n"
    "After all tool calls finish, your FINAL reply must be a single JSON "
    "object matching the declared output_schema. No prose, no markdown "
    "fences, no explanation — JSON only. For Alembic migrations, the "
    "namespace is `sa.*` (e.g. `sa.Column`, `sa.Integer()`); `op.column` "
    "does not exist."
)


# Observed weakness: after tool calls, often emits a natural-language summary
# instead of JSON matching the schema. response_format=json_object is
# respected on single-turn calls but flakier on multi-turn tool loops.
GEMINI_FLASH_HINT = (
    "\n\n--- Model-specific guidance ---\n"
    "When the task involves tool calls, after all tools are invoked, your "
    "FINAL reply must be ONLY the JSON object that matches the declared "
    "output_schema. Do not restate what you did, do not wrap in markdown, "
    "do not apologise — emit the raw JSON object and nothing else."
)


# Gemini 3.x thinking models ship tool-call thought_signatures that must
# round-trip in the history. When history is rebuilt by an adapter (as in
# shadow-gentcore), the signatures drop and the API rejects subsequent turns.
# Prefer flash variants for tool-heavy workflows until we have a
# signature-preserving adapter.
GEMINI_PRO_THINKING_HINT = (
    "\n\n--- Model-specific guidance ---\n"
    "Respond with a single JSON object matching the declared output_schema. "
    "If you need to call a tool, return exactly one tool_call and wait; do "
    "not mix narrative thinking with tool calls in the same turn."
)


# Observed weakness: can produce schema-compliant JSON but sometimes emits
# trailing commentary after the closing brace. Reminder keeps it tight.
GLM_HINT = (
    "\n\n--- Model-specific guidance ---\n"
    "Your FINAL reply must be exactly one JSON object matching the declared "
    "output_schema — no leading or trailing text, no markdown fences. After "
    "all tool calls complete, emit the JSON object and stop."
)


def get_model_hint(model_id: str | None) -> str:
    """Return a system-prompt suffix tailored to the given model.

    Returns empty string for models not in the hint registry (the default
    for Claude Opus/Sonnet, GPT-5+, etc. — they follow schemas well).
    """
    m = (model_id or "").lower()
    if not m:
        return ""

    # Minimax M2.7 (and siblings)
    if "m2.7" in m or m.startswith("minimax"):
        return MINIMAX_HINT

    # Gemini flash family
    if "gemini" in m and "flash" in m:
        return GEMINI_FLASH_HINT

    # Gemini 3.x pro (thinking) variants
    if "gemini" in m and ("pro" in m or "-pro" in m) and ("3" in m or "thinking" in m):
        return GEMINI_PRO_THINKING_HINT

    # GLM family (glm-4.5, glm-4.6, glm-5, glm-5.1, glm-4.5-flash, ...)
    if m.startswith("glm-") or m.startswith("glm_"):
        return GLM_HINT

    return ""
