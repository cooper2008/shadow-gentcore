"""Tests for react-mode empty-object schema-coerce guard.

Prior to this coverage the react loop accepted literal `{}` as a final
answer when output_schema was declared — `_is_json_like("{}")` returned
True because the string parses as valid JSON. That masked a common
GLM/MiniMax failure mode where the model closes a turn with empty
JSON after running tool calls, discarding its work and leaving the
downstream gate to fail with `coverage=0`.

`_satisfies_schema_shape` tightens the check: empty dicts, non-dicts,
and dicts missing every declared `required` key all trigger the
schema-coerce retry.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness.core.modes.react import (
    ReActStrategy,
    _is_json_like,
    _minimal_example_for_schema,
    _minimal_example_for_value,
    _satisfies_schema_shape,
)


class TestIsJsonLike:
    def test_accepts_braced(self) -> None:
        assert _is_json_like("{}") is True
        assert _is_json_like("{\"a\":1}") is True

    def test_rejects_non_braced(self) -> None:
        assert _is_json_like("plain prose") is False
        assert _is_json_like("[]") is False  # list not dict


class TestSatisfiesSchemaShape:
    def _schema(self) -> dict:
        return {"type": "object", "required": ["knowledge_map", "coverage", "gaps"]}

    def test_empty_dict_fails_required_schema(self) -> None:
        """The exact GLM/MiniMax failure mode: literal {} closing the turn."""
        assert _satisfies_schema_shape("{}", self._schema()) is False

    def test_dict_with_one_required_field_passes(self) -> None:
        assert _satisfies_schema_shape('{"knowledge_map": {}}', self._schema()) is True

    def test_dict_without_any_required_field_fails(self) -> None:
        assert _satisfies_schema_shape('{"unrelated": "stuff"}', self._schema()) is False

    def test_non_json_fails(self) -> None:
        assert _satisfies_schema_shape("not json", self._schema()) is False

    def test_empty_string_fails(self) -> None:
        assert _satisfies_schema_shape("", self._schema()) is False

    def test_none_schema_fails(self) -> None:
        """Without a schema we have no grounds to demand shape — let the
        caller's _is_json_like path handle it."""
        assert _satisfies_schema_shape("{}", None) is False

    def test_schema_without_required_accepts_empty_dict(self) -> None:
        """No `required` declared → any dict counts as satisfying."""
        assert _satisfies_schema_shape("{}", {"type": "object"}) is True

    def test_list_output_fails(self) -> None:
        """Output_schema is object — a bare list isn't a valid answer."""
        assert _satisfies_schema_shape("[]", self._schema()) is False

    def test_surrounding_whitespace_tolerated(self) -> None:
        assert _satisfies_schema_shape('  {"knowledge_map": 1}\n', self._schema()) is True


# ── Minimal-example generator (used by coerce attempt #3) ──────────────────


class TestMinimalExampleGenerator:
    def test_object_with_required_strings(self) -> None:
        ex = _minimal_example_for_schema(
            {"type": "object", "required": ["name", "purpose"],
             "properties": {"name": {"type": "string"}, "purpose": {"type": "string"}}}
        )
        assert ex == {"name": "...", "purpose": "..."}

    def test_object_with_required_array_of_required_objects(self) -> None:
        ex = _minimal_example_for_schema({
            "type": "object",
            "required": ["agent_roster"],
            "properties": {
                "agent_roster": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "category"],
                        "properties": {
                            "name": {"type": "string"},
                            "category": {"type": "string", "enum": ["reasoning", "ops"]},
                        },
                    },
                },
            },
        })
        # Single example item shows the shape — vendor can copy + replace values
        assert ex == {"agent_roster": [{"name": "...", "category": "reasoning"}]}

    def test_enum_picks_first_value(self) -> None:
        v = _minimal_example_for_value({"type": "string", "enum": ["alpha", "beta"]})
        assert v == "alpha"

    def test_integer_default_zero(self) -> None:
        v = _minimal_example_for_value({"type": "integer"})
        assert v == 0

    def test_boolean_default_false(self) -> None:
        v = _minimal_example_for_value({"type": "boolean"})
        assert v is False

    def test_array_no_required_items_returns_empty(self) -> None:
        v = _minimal_example_for_value({"type": "array", "items": {"type": "string"}})
        assert v == []

    def test_nested_object_recurses(self) -> None:
        ex = _minimal_example_for_schema({
            "type": "object",
            "required": ["design_quality"],
            "properties": {
                "design_quality": {
                    "type": "object",
                    "required": ["agent_count", "dag_valid"],
                    "properties": {
                        "agent_count": {"type": "integer"},
                        "dag_valid": {"type": "boolean"},
                    },
                },
            },
        })
        assert ex == {"design_quality": {"agent_count": 0, "dag_valid": False}}

    def test_non_object_root_returns_empty(self) -> None:
        # Root must be an object — anything else falls back to {}
        assert _minimal_example_for_schema({"type": "string"}) == {}
        assert _minimal_example_for_schema(None) == {}


# ── Multi-attempt schema-coerce escalation ────────────────────────────────


class _StubProvider:
    """Captures call history; returns scripted responses in order."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"messages": list(messages), "kwargs": dict(kwargs)})
        if self._responses:
            return self._responses.pop(0)
        return {"content": "{}", "tool_calls": [], "tokens_used": 1}


_SCHEMA = {
    "type": "object",
    "required": ["agent_roster", "design_quality"],
    "properties": {
        "agent_roster": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
        },
        "design_quality": {
            "type": "object",
            "required": ["agent_count", "dag_valid"],
            "properties": {
                "agent_count": {"type": "integer"},
                "dag_valid": {"type": "boolean"},
            },
        },
    },
}


@pytest.mark.asyncio
async def test_coerce_succeeds_on_attempt_one() -> None:
    """When attempt 1 produces valid output, no further attempts run."""
    provider = _StubProvider([
        # main step → empty `{}` (the GLM failure mode)
        {"content": "{}", "tool_calls": [], "tokens_used": 5},
        # coerce attempt 1 → success
        {"content": '{"agent_roster": [{"name": "x"}], "design_quality": {"agent_count": 1, "dag_valid": true}}',
         "tool_calls": [], "tokens_used": 10},
    ])
    result = await ReActStrategy(max_steps=1).execute(
        messages=[{"role": "user", "content": "do task"}],
        provider=provider,
        tool_executor=None,
        output_schema=_SCHEMA,
        declared_tools=[],
    )
    # 1 main + 1 coerce attempt = 2 calls
    assert len(provider.calls) == 2
    assert "agent_roster" in result["content"]
    coerce_steps = [s for s in result["steps"] if s.get("type") == "schema_coerce"]
    assert len(coerce_steps) == 1
    assert coerce_steps[0]["attempt"] == 1


@pytest.mark.asyncio
async def test_coerce_escalates_until_success() -> None:
    """When attempts 1+2 still emit `{}`, attempt 3's concrete-example prompt
    is the last shot. Verify all 3 attempts fire and the last one wins.
    """
    provider = _StubProvider([
        {"content": "{}", "tool_calls": [], "tokens_used": 5},   # main
        {"content": "{}", "tool_calls": [], "tokens_used": 5},   # coerce 1: still empty
        {"content": "{}", "tool_calls": [], "tokens_used": 5},   # coerce 2: still empty
        {"content": '{"agent_roster": [{"name": "p"}], "design_quality": {"agent_count": 1, "dag_valid": true}}',
         "tool_calls": [], "tokens_used": 12},                   # coerce 3: success
    ])
    result = await ReActStrategy(max_steps=1).execute(
        messages=[{"role": "user", "content": "do task"}],
        provider=provider,
        tool_executor=None,
        output_schema=_SCHEMA,
        declared_tools=[],
    )
    # 1 main + 3 coerce = 4 calls
    assert len(provider.calls) == 4
    assert "agent_roster" in result["content"]
    coerce_steps = [s for s in result["steps"] if s.get("type") == "schema_coerce"]
    assert [s["attempt"] for s in coerce_steps] == [1, 2, 3]


@pytest.mark.asyncio
async def test_coerce_attempt_two_includes_missing_field_callout() -> None:
    """Attempt 2's user prompt must explicitly list required fields so weak
    vendors stop emitting `{}` because they "didn't know what to fill in"."""
    provider = _StubProvider([
        {"content": "{}", "tool_calls": [], "tokens_used": 5},
        {"content": "{}", "tool_calls": [], "tokens_used": 5},
        {"content": '{"agent_roster": [{"name": "x"}], "design_quality": {"agent_count": 1, "dag_valid": true}}',
         "tool_calls": [], "tokens_used": 10},
    ])
    await ReActStrategy(max_steps=1).execute(
        messages=[{"role": "user", "content": "do"}],
        provider=provider,
        tool_executor=None,
        output_schema=_SCHEMA,
        declared_tools=[],
    )
    # Call 2 is the FIRST coerce. Its message list ends with the attempt-1 prompt.
    # Call 3 (attempt 2) ends with the missing-fields callout.
    third_call_messages = provider.calls[2]["messages"]
    last_user = next(m for m in reversed(third_call_messages) if m.get("role") == "user")
    assert "missing required fields" in last_user["content"].lower() \
        or "required fields" in last_user["content"].lower()
    assert "agent_roster" in last_user["content"]


@pytest.mark.asyncio
async def test_coerce_attempt_three_includes_concrete_example() -> None:
    """Attempt 3 must give the model a concrete example to copy from."""
    provider = _StubProvider([
        {"content": "{}", "tool_calls": [], "tokens_used": 5},
        {"content": "{}", "tool_calls": [], "tokens_used": 5},
        {"content": "{}", "tool_calls": [], "tokens_used": 5},
        {"content": '{"agent_roster": [{"name": "x"}], "design_quality": {"agent_count": 1, "dag_valid": true}}',
         "tool_calls": [], "tokens_used": 10},
    ])
    await ReActStrategy(max_steps=1).execute(
        messages=[{"role": "user", "content": "do"}],
        provider=provider,
        tool_executor=None,
        output_schema=_SCHEMA,
        declared_tools=[],
    )
    fourth_call_messages = provider.calls[3]["messages"]
    last_user = next(m for m in reversed(fourth_call_messages) if m.get("role") == "user")
    assert "concrete example" in last_user["content"].lower() \
        or "final attempt" in last_user["content"].lower()
    # Must contain a literal serialized example (e.g. with the placeholder "...")
    assert '"name"' in last_user["content"]


@pytest.mark.asyncio
async def test_coerce_returns_last_content_when_all_attempts_fail() -> None:
    """If every coerce attempt emits `{}`, return the last content rather
    than crashing. The downstream gate will surface the failure."""
    provider = _StubProvider([
        {"content": "{}", "tool_calls": [], "tokens_used": 5},
        {"content": "{}", "tool_calls": [], "tokens_used": 5},
        {"content": "{}", "tool_calls": [], "tokens_used": 5},
        {"content": "{}", "tool_calls": [], "tokens_used": 5},
    ])
    result = await ReActStrategy(max_steps=1).execute(
        messages=[{"role": "user", "content": "do"}],
        provider=provider,
        tool_executor=None,
        output_schema=_SCHEMA,
        declared_tools=[],
    )
    assert result["content"] == "{}"
    coerce_steps = [s for s in result["steps"] if s.get("type") == "schema_coerce"]
    assert len(coerce_steps) == 3  # all 3 attempts ran


class _ScriptedProvider:
    """Provider that may raise per-call instead of returning a response."""

    def __init__(self, plan: list[dict[str, Any] | Exception]) -> None:
        self._plan = list(plan)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"messages": list(messages), "kwargs": dict(kwargs)})
        if not self._plan:
            return {"content": "{}", "tool_calls": [], "tokens_used": 1}
        item = self._plan.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.mark.asyncio
async def test_coerce_continues_through_transient_api_errors() -> None:
    """Vendors (GLM/MiniMax via BigModel) intermittently 400/500 mid-coerce.
    A single transient error must NOT abort the escalation — the next prompt
    might still succeed. Pre-fix one network blip killed the whole retry.
    """
    provider = _ScriptedProvider([
        {"content": "{}", "tool_calls": [], "tokens_used": 5},   # main
        RuntimeError("Error code: 400 - 网络错误"),                # coerce 1: API error
        {"content": "{}", "tool_calls": [], "tokens_used": 5},   # coerce 2: still empty
        {"content": '{"agent_roster": [{"name": "p"}], "design_quality": {"agent_count": 1, "dag_valid": true}}',
         "tool_calls": [], "tokens_used": 12},                   # coerce 3: success
    ])
    result = await ReActStrategy(max_steps=1).execute(
        messages=[{"role": "user", "content": "do"}],
        provider=provider,
        tool_executor=None,
        output_schema=_SCHEMA,
        declared_tools=[],
    )
    # All 3 coerce attempts ran despite the API error in attempt 1
    assert len(provider.calls) == 4  # 1 main + 3 coerce
    assert "agent_roster" in result["content"]
    coerce_steps = [s for s in result["steps"] if s.get("type", "").startswith("schema_coerce")]
    # Should record an error step + 2 normal coerce steps
    error_steps = [s for s in coerce_steps if s["type"] == "schema_coerce_error"]
    success_steps = [s for s in coerce_steps if s["type"] == "schema_coerce"]
    assert len(error_steps) == 1
    assert len(success_steps) == 2


@pytest.mark.asyncio
async def test_coerce_skipped_when_first_response_satisfies() -> None:
    """When the main step already produces valid output, no coerce fires."""
    provider = _StubProvider([
        {"content": '{"agent_roster": [{"name": "x"}], "design_quality": {"agent_count": 1, "dag_valid": true}}',
         "tool_calls": [], "tokens_used": 10},
    ])
    result = await ReActStrategy(max_steps=1).execute(
        messages=[{"role": "user", "content": "do"}],
        provider=provider,
        tool_executor=None,
        output_schema=_SCHEMA,
        declared_tools=[],
    )
    assert len(provider.calls) == 1  # only the main step
    coerce_steps = [s for s in result["steps"] if s.get("type") == "schema_coerce"]
    assert coerce_steps == []
