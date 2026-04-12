"""ModeDispatcher — selects execution strategy based on agent manifest configuration."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from harness.core.modes.base import ExecutionStrategy


class ModeDispatcher:
    """Dispatches to the correct ExecutionStrategy based on manifest execution_mode config.

    Supports: react, plan_execute, chain_of_thought, self_ask, tree_of_thought.
    Falls back to react if unrecognized.
    """

    def __init__(self) -> None:
        self._strategy_registry: dict[str, type[ExecutionStrategy]] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register built-in execution strategies."""
        from harness.core.modes.react import ReActStrategy
        from harness.core.modes.plan_execute import PlanExecuteStrategy
        from harness.core.modes.chain_of_thought import ChainOfThoughtStrategy

        self._strategy_registry["react"] = ReActStrategy
        self._strategy_registry["plan_execute"] = PlanExecuteStrategy
        self._strategy_registry["chain_of_thought"] = ChainOfThoughtStrategy

    def register(self, name: str, strategy_cls: type[ExecutionStrategy]) -> None:
        """Register a custom execution strategy."""
        self._strategy_registry[name] = strategy_cls

    def dispatch(self, execution_mode: dict[str, Any] | None = None) -> ExecutionStrategy:
        """Select and instantiate the appropriate ExecutionStrategy.

        Args:
            execution_mode: Dict with 'strategy' key and optional config.
                           Falls back to ReAct if None or unrecognized.

        Returns:
            An instantiated ExecutionStrategy ready for execution.
        """
        if execution_mode is None:
            execution_mode = {}

        # Support plain string execution_mode (e.g. "react" from domain agent manifests)
        if isinstance(execution_mode, str):
            execution_mode = {"primary": execution_mode}

        # Support both "strategy" and "primary" keys for mode selection
        strategy_name = execution_mode.get("strategy") or execution_mode.get("primary", "react")
        strategy_cls = self._strategy_registry.get(strategy_name)

        if strategy_cls is None:
            # Fall back to react
            from harness.core.modes.react import ReActStrategy
            strategy_cls = ReActStrategy

        # Map manifest config keys to strategy constructor params
        config = {k: v for k, v in execution_mode.items() if k not in ("strategy", "primary", "fallback")}
        # Map max_react_steps → max_steps for ReActStrategy
        if "max_react_steps" in config:
            config.setdefault("max_steps", config.pop("max_react_steps"))
        # Map max_plan_steps → max_plan_steps (already matches PlanExecuteStrategy)
        return strategy_cls(**config)

    @property
    def available_strategies(self) -> list[str]:
        """Return list of registered strategy names."""
        return list(self._strategy_registry.keys())
