"""Tests for react-mode mid-run message-history compaction.

Without compaction, react loops accumulate tool observations across steps
and eventually overflow the model's context window in long sequences.
The new ``execution_mode.compaction`` block on AgentManifest opts an
agent into automatic mid-run compaction:

  * ``trigger_token_estimate`` — message-history token estimate that fires compaction
  * ``keep_last_n_turns``     — react rounds (assistant + tool_result) kept verbatim
  * ``strategy``              — ``summarize_oldest`` (LLM call), ``drop_oldest``, or ``none``

These tests cover the helpers and the loop integration. The
strategy=None default means existing agents are unaffected; we explicitly
exercise the opt-in path.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness.core.modes.react import (
    ReActStrategy,
    _compact_message_history,
    _estimate_message_tokens,
    _serialize_messages_for_summary,
)


def _round(step: int, observation_text: str) -> list[dict[str, Any]]:
    """Build one react round: assistant tool_use + user tool_result."""
    return [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": f"thinking step {step}"},
                {
                    "type": "tool_use",
                    "id": f"tool_{step}",
                    "name": "search_code",
                    "input": {"pattern": f"pattern_{step}"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": f"tool_{step}",
                    "content": observation_text,
                }
            ],
        },
    ]


def _history(num_rounds: int, observation_size: int = 500) -> list[dict[str, Any]]:
    """Build a head user task + ``num_rounds`` react rounds."""
    msgs: list[dict[str, Any]] = [
        {"role": "user", "content": "Implement the auth flow."}
    ]
    for i in range(num_rounds):
        msgs.extend(_round(i, "X" * observation_size))
    return msgs


class FakeProvider:
    """Echo provider that records summarization calls."""

    def __init__(self, summary_text: str = "Summary: looked at auth.py and login.py.") -> None:
        self.summary_text = summary_text
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(list(messages))
        return {"content": self.summary_text, "tokens_used": 42, "tool_calls": []}


class TestEstimateMessageTokens:
    def test_handles_string_content(self) -> None:
        msgs = [{"role": "user", "content": "X" * 400}]
        assert _estimate_message_tokens(msgs) == 100

    def test_handles_block_content_with_text_and_tool_use(self) -> None:
        msgs = [{
            "role": "assistant",
            "content": [
                {"type": "text", "text": "X" * 200},
                {"type": "tool_use", "name": "x", "input": {"q": "Y" * 200}},
            ],
        }]
        assert _estimate_message_tokens(msgs) >= 50

    def test_returns_zero_for_empty(self) -> None:
        assert _estimate_message_tokens([]) == 0


class TestSerializeForSummary:
    def test_renders_text_blocks(self) -> None:
        msgs = [{"role": "assistant", "content": [{"type": "text", "text": "hello world"}]}]
        out = _serialize_messages_for_summary(msgs)
        assert "[assistant:text]" in out and "hello world" in out

    def test_renders_tool_use_with_arg_keys(self) -> None:
        msgs = [{
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "grep", "input": {"pattern": "x", "path": "."}}],
        }]
        out = _serialize_messages_for_summary(msgs)
        assert "tool_use" in out and "grep" in out and "pattern" in out

    def test_truncates_giant_tool_result(self) -> None:
        msgs = [{
            "role": "user",
            "content": [{"type": "tool_result", "content": "Z" * 5000}],
        }]
        out = _serialize_messages_for_summary(msgs)
        assert len(out) < 1000  # truncated, not 5000+ chars


class TestCompactMessageHistory:
    @pytest.mark.asyncio
    async def test_strategy_none_returns_unchanged(self) -> None:
        msgs = _history(num_rounds=10)
        provider = FakeProvider()
        out = await _compact_message_history(msgs, provider, keep_last_n_rounds=2, strategy="none")
        assert out is msgs
        assert provider.calls == []  # no summarization call

    @pytest.mark.asyncio
    async def test_short_history_not_compacted(self) -> None:
        """With only 2 rounds + head and keep_last_n=2, there's no middle to compact."""
        msgs = _history(num_rounds=2)
        provider = FakeProvider()
        out = await _compact_message_history(
            msgs, provider, keep_last_n_rounds=2, strategy="summarize_oldest"
        )
        assert out is msgs

    @pytest.mark.asyncio
    async def test_drop_oldest_strategy_skips_provider(self) -> None:
        msgs = _history(num_rounds=8)
        provider = FakeProvider()
        out = await _compact_message_history(
            msgs, provider, keep_last_n_rounds=2, strategy="drop_oldest"
        )
        assert provider.calls == []  # no LLM call for cheap strategy
        # head replaced + last 2 rounds kept = 1 + 4 = 5 messages
        assert len(out) == 5
        # Synthetic message preserves the original task plus a marker
        assert "Implement the auth flow" in out[0]["content"]
        assert "Auto-compacted" in out[0]["content"]

    @pytest.mark.asyncio
    async def test_summarize_oldest_calls_provider_and_inlines_summary(self) -> None:
        msgs = _history(num_rounds=8)
        provider = FakeProvider(summary_text="Examined auth.py — found bcrypt usage.")
        out = await _compact_message_history(
            msgs, provider, keep_last_n_rounds=2, strategy="summarize_oldest"
        )
        assert len(provider.calls) == 1
        # Summary text appears in the synthetic head message
        assert "bcrypt usage" in out[0]["content"]
        assert "Implement the auth flow" in out[0]["content"]
        # Last 2 rounds preserved verbatim (4 messages)
        assert len(out) == 5
        assert out[-1]["role"] == "user"  # tool_result of last round
        assert out[-2]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_summarize_failure_falls_back_to_original(self) -> None:
        """When the provider raises, we keep original messages — fail-open."""
        class BrokenProvider:
            async def chat(self, *a: Any, **kw: Any) -> dict[str, Any]:
                raise RuntimeError("provider down")

        msgs = _history(num_rounds=8)
        out = await _compact_message_history(
            msgs, BrokenProvider(), keep_last_n_rounds=2, strategy="summarize_oldest"
        )
        assert out is msgs

    @pytest.mark.asyncio
    async def test_empty_summary_falls_back_to_original(self) -> None:
        msgs = _history(num_rounds=8)
        provider = FakeProvider(summary_text="   ")
        out = await _compact_message_history(
            msgs, provider, keep_last_n_rounds=2, strategy="summarize_oldest"
        )
        assert out is msgs


class TestReActStrategyCompactionConfig:
    def test_default_compaction_disabled(self) -> None:
        s = ReActStrategy()
        assert s._compaction_trigger is None
        assert s._compaction_strategy == "summarize_oldest"
        assert s._compaction_keep_last == 2

    def test_compaction_dict_parsed(self) -> None:
        s = ReActStrategy(
            compaction={"strategy": "drop_oldest", "keep_last_n_turns": 3, "trigger_token_estimate": 50000}
        )
        assert s._compaction_strategy == "drop_oldest"
        assert s._compaction_keep_last == 3
        assert s._compaction_trigger == 50000

    def test_compaction_pydantic_model_parsed(self) -> None:
        from agent_contracts.manifests.agent_manifest import CompactionConfig

        cfg = CompactionConfig(
            strategy="drop_oldest", keep_last_n_turns=3, trigger_token_estimate=42000
        )
        s = ReActStrategy(compaction=cfg)
        assert s._compaction_strategy == "drop_oldest"
        assert s._compaction_keep_last == 3
        assert s._compaction_trigger == 42000


class TestReActStrategyCompactionLoop:
    @pytest.mark.asyncio
    async def test_compaction_fires_when_history_exceeds_trigger(self) -> None:
        """End-to-end: a react loop with a tiny trigger compacts mid-run.

        We use a fake provider that emits a tool_call on every step until
        the loop terminates. After enough rounds the message history
        exceeds the trigger and a 'compaction' step appears in steps[].
        """
        from harness.core.modes.react import ReActStrategy

        class StepProvider:
            """Emits tool calls for N steps, then returns final text."""

            def __init__(self, steps_with_tools: int) -> None:
                self.n = steps_with_tools
                self.call = 0
                self.summary_calls = 0

            async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
                # Distinguish summarization calls (no tools kwarg) from main loop
                if "tools" not in kwargs and self.call >= self.n:
                    self.summary_calls += 1
                    return {"content": "compacted summary", "tokens_used": 1, "tool_calls": []}
                self.call += 1
                if self.call <= self.n:
                    return {
                        "content": f"step {self.call}",
                        "tokens_used": 10,
                        "tool_calls": [{
                            "id": f"t{self.call}",
                            "name": "search_code",
                            "arguments": {"pattern": "x"},
                        }],
                    }
                return {"content": "done", "tokens_used": 5, "tool_calls": []}

        class FakeExecutor:
            _adapters: dict[str, Any] = {"search_code": object()}

            async def execute(self, tc: dict[str, Any]) -> dict[str, Any]:
                # Big observation so the trigger fires quickly
                return {"output": "OBSERVATION_" + ("Y" * 4000)}

        strategy = ReActStrategy(
            max_steps=8,
            compaction={
                "strategy": "summarize_oldest",
                "keep_last_n_turns": 2,
                "trigger_token_estimate": 2000,
            },
        )
        provider = StepProvider(steps_with_tools=5)
        result = await strategy.execute(
            messages=[{"role": "user", "content": "find auth"}],
            provider=provider,
            tool_executor=FakeExecutor(),
            declared_tools=["search_code"],
        )

        compaction_steps = [s for s in result["steps"] if s.get("type") == "compaction"]
        assert len(compaction_steps) >= 1, (
            f"expected at least one compaction step in {[s.get('type') for s in result['steps']]}"
        )
        first = compaction_steps[0]
        assert first["strategy"] == "summarize_oldest"
        assert first["messages_after"] < first["messages_before"]
        assert first["estimated_tokens_after"] < first["estimated_tokens_before"]

    @pytest.mark.asyncio
    async def test_no_trigger_means_no_compaction(self) -> None:
        """Backwards compat: existing agents (no compaction config) see no behavior change."""
        from harness.core.modes.react import ReActStrategy

        class OneStepProvider:
            async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
                return {"content": "done", "tokens_used": 1, "tool_calls": []}

        strategy = ReActStrategy(max_steps=3)  # no compaction kwarg
        result = await strategy.execute(
            messages=[{"role": "user", "content": "x"}],
            provider=OneStepProvider(),
            tool_executor=None,
            declared_tools=[],
        )
        assert not any(s.get("type") == "compaction" for s in result["steps"])
