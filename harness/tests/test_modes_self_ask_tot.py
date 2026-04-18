"""Tests for self_ask + tree_of_thought execution strategies."""

from __future__ import annotations

from typing import Any

import pytest

from harness.core.mode_dispatcher import ModeDispatcher
from harness.core.modes.self_ask import SelfAskStrategy, STOP_MARKER
from harness.core.modes.tree_of_thought import TreeOfThoughtStrategy


# ── Fake provider that cycles through scripted replies ────────────────────


class ScriptedProvider:
    """Returns pre-scripted content strings in order, one per chat() call."""

    def __init__(self, scripts: list[str]) -> None:
        self._scripts = list(scripts)
        self.call_log: list[list[dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, Any]], **_kwargs: Any) -> dict[str, Any]:
        self.call_log.append(list(messages))
        if not self._scripts:
            return {"content": "", "tokens_used": 0, "tool_calls": []}
        content = self._scripts.pop(0)
        return {"content": content, "tokens_used": 10, "tool_calls": []}


# ── SelfAskStrategy ──────────────────────────────────────────────────────


class TestSelfAsk:
    @pytest.mark.asyncio
    async def test_decompose_then_answer_then_compose(self) -> None:
        provider = ScriptedProvider([
            "Follow up: what does the user mean by 'fast'?",
            "They likely mean sub-100ms p99.",
            f"{STOP_MARKER}\nI have enough context.",
            '{"answer": "use cursor pagination"}',
        ])
        strategy = SelfAskStrategy(max_rounds=3)
        result = await strategy.execute(
            messages=[{"role": "user", "content": "design a fast reviews endpoint"}],
            provider=provider,
        )
        assert result["content"] == '{"answer": "use cursor pagination"}'
        step_types = [s["type"] for s in result["steps"]]
        assert step_types == ["decompose", "answer", "decompose", "compose"]
        assert result["tokens_used"] > 0

    @pytest.mark.asyncio
    async def test_stops_when_no_followup(self) -> None:
        provider = ScriptedProvider([
            "I already know enough.",  # no follow-up marker → loop exits
            "final JSON here",
        ])
        strategy = SelfAskStrategy(max_rounds=3)
        result = await strategy.execute(
            messages=[{"role": "user", "content": "trivial task"}],
            provider=provider,
        )
        assert result["content"] == "final JSON here"
        step_types = [s["type"] for s in result["steps"]]
        assert step_types == ["decompose", "compose"]

    @pytest.mark.asyncio
    async def test_respects_max_rounds(self) -> None:
        # Every round emits a follow-up — we should stop at max_rounds
        provider = ScriptedProvider([
            "Follow up: q1", "a1",
            "Follow up: q2", "a2",
            "final",
        ])
        strategy = SelfAskStrategy(max_rounds=2)
        result = await strategy.execute(
            messages=[{"role": "user", "content": "t"}],
            provider=provider,
        )
        # 2 decompose rounds + 2 answers + 1 compose = 5 steps
        assert len(result["steps"]) == 5
        assert result["content"] == "final"

    @pytest.mark.asyncio
    async def test_output_schema_forwarded_on_compose(self) -> None:
        provider = ScriptedProvider([
            "no questions",
            '{"all_passed": true}',
        ])
        strategy = SelfAskStrategy(max_rounds=2)
        schema = {"type": "object", "properties": {"all_passed": {"type": "boolean"}}}
        result = await strategy.execute(
            messages=[{"role": "user", "content": "t"}],
            provider=provider,
            output_schema=schema,
        )
        # Final message should reference the schema in the composition prompt
        last_messages = provider.call_log[-1]
        compose_prompt = last_messages[-1]["content"]
        assert "JSON object matching this schema" in compose_prompt
        assert result["content"] == '{"all_passed": true}'

    def test_rejects_invalid_max_rounds(self) -> None:
        with pytest.raises(ValueError):
            SelfAskStrategy(max_rounds=0)


# ── TreeOfThoughtStrategy ────────────────────────────────────────────────


class TestTreeOfThought:
    @pytest.mark.asyncio
    async def test_vote_selects_candidate_and_composes(self) -> None:
        provider = ScriptedProvider([
            "cand 1: keyset pagination",
            "cand 2: offset pagination",
            "cand 3: window functions",
            "PICK=1\nREASON=keyset is stable",
            '{"picked": "keyset"}',
        ])
        strategy = TreeOfThoughtStrategy(num_branches=3, selection="vote")
        result = await strategy.execute(
            messages=[{"role": "user", "content": "how to paginate?"}],
            provider=provider,
        )
        assert result["content"] == '{"picked": "keyset"}'
        step_types = [s["type"] for s in result["steps"]]
        assert step_types == ["branch", "branch", "branch", "select", "compose"]
        selection_step = next(s for s in result["steps"] if s["type"] == "select")
        assert selection_step["picked_index"] == 0
        assert "keyset is stable" in selection_step["rationale"]

    @pytest.mark.asyncio
    async def test_first_selection_skips_vote(self) -> None:
        provider = ScriptedProvider([
            "cand A",
            "cand B",
            "final output",
        ])
        strategy = TreeOfThoughtStrategy(num_branches=2, selection="first")
        result = await strategy.execute(
            messages=[{"role": "user", "content": "t"}],
            provider=provider,
        )
        # 2 branches + 1 select + 1 compose = 4 steps, no vote
        assert len(result["steps"]) == 4
        assert result["content"] == "final output"

    @pytest.mark.asyncio
    async def test_longest_selection_picks_biggest(self) -> None:
        provider = ScriptedProvider([
            "short",
            "this candidate is much longer than the short one and wins on length",
            "another medium-length thing",
            "final",
        ])
        strategy = TreeOfThoughtStrategy(num_branches=3, selection="longest")
        result = await strategy.execute(
            messages=[{"role": "user", "content": "t"}],
            provider=provider,
        )
        select_step = next(s for s in result["steps"] if s["type"] == "select")
        assert select_step["picked_index"] == 1

    @pytest.mark.asyncio
    async def test_output_schema_forwarded_on_compose(self) -> None:
        provider = ScriptedProvider([
            "cand 1", "cand 2",
            "final JSON",
        ])
        strategy = TreeOfThoughtStrategy(num_branches=2, selection="first")
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        await strategy.execute(
            messages=[{"role": "user", "content": "t"}],
            provider=provider,
            output_schema=schema,
        )
        last_messages = provider.call_log[-1]
        compose_prompt = last_messages[-1]["content"]
        assert "JSON object matching this schema" in compose_prompt

    def test_rejects_num_branches_below_two(self) -> None:
        with pytest.raises(ValueError):
            TreeOfThoughtStrategy(num_branches=1)

    def test_rejects_unknown_selection_method(self) -> None:
        with pytest.raises(ValueError):
            TreeOfThoughtStrategy(num_branches=2, selection="random")


# ── ModeDispatcher wiring ────────────────────────────────────────────────


class TestModeDispatcherWiring:
    def test_self_ask_registered(self) -> None:
        dispatcher = ModeDispatcher()
        assert "self_ask" in dispatcher.available_strategies
        strategy = dispatcher.dispatch({"primary": "self_ask"})
        assert strategy.name == "self_ask"

    def test_tree_of_thought_registered(self) -> None:
        dispatcher = ModeDispatcher()
        assert "tree_of_thought" in dispatcher.available_strategies
        strategy = dispatcher.dispatch({"primary": "tree_of_thought"})
        assert strategy.name == "tree_of_thought"

    def test_self_ask_config_passed_through(self) -> None:
        dispatcher = ModeDispatcher()
        strategy = dispatcher.dispatch({"primary": "self_ask", "max_rounds": 5})
        assert isinstance(strategy, SelfAskStrategy)
        assert strategy.max_rounds == 5

    def test_tree_of_thought_config_passed_through(self) -> None:
        dispatcher = ModeDispatcher()
        strategy = dispatcher.dispatch({
            "primary": "tree_of_thought",
            "num_branches": 4,
            "selection": "longest",
        })
        assert isinstance(strategy, TreeOfThoughtStrategy)
        assert strategy.num_branches == 4
        assert strategy.selection == "longest"
