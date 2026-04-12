"""Tests for ModeDispatcher and execution strategies."""

from __future__ import annotations

import pytest

from harness.core.mode_dispatcher import ModeDispatcher
from harness.core.modes.base import ExecutionStrategy
from harness.core.modes.react import ReActStrategy
from harness.core.modes.plan_execute import PlanExecuteStrategy
from harness.core.modes.chain_of_thought import ChainOfThoughtStrategy


class TestModeDispatcher:
    def test_default_strategies_registered(self) -> None:
        dispatcher = ModeDispatcher()
        assert "react" in dispatcher.available_strategies
        assert "plan_execute" in dispatcher.available_strategies
        assert "chain_of_thought" in dispatcher.available_strategies

    def test_dispatch_react(self) -> None:
        dispatcher = ModeDispatcher()
        strategy = dispatcher.dispatch({"strategy": "react"})
        assert isinstance(strategy, ReActStrategy)
        assert strategy.name == "react"

    def test_dispatch_plan_execute(self) -> None:
        dispatcher = ModeDispatcher()
        strategy = dispatcher.dispatch({"strategy": "plan_execute"})
        assert isinstance(strategy, PlanExecuteStrategy)
        assert strategy.name == "plan_execute"

    def test_dispatch_chain_of_thought(self) -> None:
        dispatcher = ModeDispatcher()
        strategy = dispatcher.dispatch({"strategy": "chain_of_thought"})
        assert isinstance(strategy, ChainOfThoughtStrategy)
        assert strategy.name == "chain_of_thought"

    def test_dispatch_none_defaults_to_react(self) -> None:
        dispatcher = ModeDispatcher()
        strategy = dispatcher.dispatch(None)
        assert isinstance(strategy, ReActStrategy)

    def test_dispatch_empty_dict_defaults_to_react(self) -> None:
        dispatcher = ModeDispatcher()
        strategy = dispatcher.dispatch({})
        assert isinstance(strategy, ReActStrategy)

    def test_dispatch_unknown_falls_back_to_react(self) -> None:
        dispatcher = ModeDispatcher()
        strategy = dispatcher.dispatch({"strategy": "nonexistent"})
        assert isinstance(strategy, ReActStrategy)

    def test_dispatch_passes_config(self) -> None:
        dispatcher = ModeDispatcher()
        strategy = dispatcher.dispatch({"strategy": "react", "max_steps": 5})
        assert isinstance(strategy, ReActStrategy)
        assert strategy.max_steps == 5

    def test_register_custom_strategy(self) -> None:
        class CustomStrategy(ExecutionStrategy):
            async def execute(self, messages, provider, tool_executor=None):
                return {"content": "custom", "tool_calls": [], "tokens_used": 0, "steps": []}
            @property
            def name(self) -> str:
                return "custom"

        dispatcher = ModeDispatcher()
        dispatcher.register("custom", CustomStrategy)
        assert "custom" in dispatcher.available_strategies
        strategy = dispatcher.dispatch({"strategy": "custom"})
        assert strategy.name == "custom"
