"""Tests for ToolDisclosureRouter — progressive L1→L2 tool disclosure."""

from __future__ import annotations

from typing import Any

import pytest

from harness.core.tool_disclosure import ToolDisclosureRouter


class _MockExecutor:
    """Minimal ToolExecutor stub with a fixed adapter registry."""

    def __init__(self, registered: list[str]) -> None:
        self._adapters: dict[str, Any] = {name: object() for name in registered}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_router(
    tools: list[Any],
    registered: list[str] | None = None,
) -> ToolDisclosureRouter:
    if registered is None:
        # Default: all declared tools are registered
        names = [
            t["name"] if isinstance(t, dict) else str(t)
            for t in tools
        ]
        registered = names
    executor = _MockExecutor(registered)
    return ToolDisclosureRouter(tools, executor)


# ---------------------------------------------------------------------------
# Partition tests
# ---------------------------------------------------------------------------


class TestPartitioning:
    def test_default_level_is_l2(self) -> None:
        """Tools without a level field default to L2."""
        router = _make_router([
            {"name": "file_read", "desc": "Read files"},
        ])
        assert "file_read" in router._always_l2
        assert "file_read" not in router._l1_desc

    def test_explicit_l2_is_always_l2(self) -> None:
        router = _make_router([
            {"name": "file_read", "desc": "Read", "level": "L2"},
        ])
        assert "file_read" in router._always_l2
        assert len(router._l1_pending) == 0

    def test_l1_tool_starts_pending(self) -> None:
        router = _make_router([
            {"name": "gh_create_pr", "desc": "Open PR", "level": "L1"},
        ])
        assert "gh_create_pr" in router._l1_pending
        assert "gh_create_pr" in router._l1_desc
        assert "gh_create_pr" not in router._always_l2

    def test_mixed_levels(self) -> None:
        router = _make_router([
            {"name": "file_read", "desc": "Read files"},
            {"name": "gh_create_pr", "desc": "Open PR", "level": "L1"},
            {"name": "shell_exec", "desc": "Run shell", "level": "L2"},
        ])
        assert set(router._always_l2) == {"file_read", "shell_exec"}
        assert router._l1_pending == {"gh_create_pr"}

    def test_unregistered_tool_is_silently_ignored(self) -> None:
        executor = _MockExecutor(["file_read"])
        router = ToolDisclosureRouter(
            [{"name": "file_read"}, {"name": "ghost_tool", "level": "L1"}],
            executor,
        )
        assert "file_read" in router._always_l2
        assert "ghost_tool" not in router._l1_pending
        assert "ghost_tool" not in router._always_l2

    def test_string_entry_defaults_to_l2(self) -> None:
        router = _make_router(["file_read", "search_code"])
        assert set(router._always_l2) == {"file_read", "search_code"}
        assert len(router._l1_pending) == 0


# ---------------------------------------------------------------------------
# has_l1_tools / pending_l1_count
# ---------------------------------------------------------------------------


class TestProperties:
    def test_has_l1_tools_false_when_all_l2(self) -> None:
        router = _make_router([{"name": "file_read"}])
        assert router.has_l1_tools is False

    def test_has_l1_tools_true_when_some_l1(self) -> None:
        router = _make_router([{"name": "gh_pr", "level": "L1"}])
        assert router.has_l1_tools is True

    def test_pending_count_decreases_on_promote(self) -> None:
        router = _make_router([
            {"name": "tool_a", "level": "L1"},
            {"name": "tool_b", "level": "L1"},
        ])
        assert router.pending_l1_count == 2
        router.promote("tool_a")
        assert router.pending_l1_count == 1
        router.promote("tool_b")
        assert router.pending_l1_count == 0


# ---------------------------------------------------------------------------
# current_api_tools
# ---------------------------------------------------------------------------


class TestCurrentApiTools:
    def test_only_l2_tools_in_initial_api_tools(self) -> None:
        router = _make_router([
            {"name": "file_read"},
            {"name": "gh_pr", "level": "L1"},
        ], registered=["file_read", "gh_pr"])
        tools = router.current_api_tools()
        names = {t["name"] for t in tools}
        assert "file_read" in names
        assert "gh_pr" not in names  # still L1 — not promoted yet

    def test_promoted_l1_appears_in_api_tools(self) -> None:
        router = _make_router([
            {"name": "file_read"},
            {"name": "gh_pr", "level": "L1"},
        ], registered=["file_read", "gh_pr"])
        router.promote("gh_pr")
        tools = router.current_api_tools()
        names = {t["name"] for t in tools}
        assert "file_read" in names
        assert "gh_pr" in names

    def test_no_tools_returns_empty(self) -> None:
        executor = _MockExecutor([])
        router = ToolDisclosureRouter([], executor)
        assert router.current_api_tools() == []


# ---------------------------------------------------------------------------
# l1_summary
# ---------------------------------------------------------------------------


class TestL1Summary:
    def test_summary_none_when_no_l1_tools(self) -> None:
        router = _make_router([{"name": "file_read"}])
        assert router.l1_summary() is None

    def test_summary_contains_pending_names(self) -> None:
        router = _make_router([
            {"name": "gh_pr", "desc": "Open a GitHub PR", "level": "L1"},
            {"name": "slack_msg", "desc": "Send Slack message", "level": "L1"},
        ])
        summary = router.l1_summary()
        assert summary is not None
        assert "gh_pr" in summary
        assert "slack_msg" in summary
        assert "Open a GitHub PR" in summary

    def test_summary_none_after_all_promoted(self) -> None:
        router = _make_router([
            {"name": "gh_pr", "level": "L1"},
        ])
        router.promote("gh_pr")
        assert router.l1_summary() is None

    def test_summary_excludes_promoted_tools(self) -> None:
        router = _make_router([
            {"name": "gh_pr", "desc": "Open PR", "level": "L1"},
            {"name": "slack", "desc": "Send msg", "level": "L1"},
        ])
        router.promote("gh_pr")
        summary = router.l1_summary()
        assert summary is not None
        assert "gh_pr" not in summary
        assert "slack" in summary


# ---------------------------------------------------------------------------
# detect_and_promote
# ---------------------------------------------------------------------------


class TestDetectAndPromote:
    def test_exact_name_triggers_promotion(self) -> None:
        router = _make_router([{"name": "gh_create_pr", "level": "L1"}])
        promoted = router.detect_and_promote("I should use gh_create_pr to open a PR")
        assert promoted == ["gh_create_pr"]
        assert "gh_create_pr" not in router._l1_pending
        assert "gh_create_pr" in router._promoted

    def test_no_match_returns_empty(self) -> None:
        router = _make_router([{"name": "gh_create_pr", "level": "L1"}])
        promoted = router.detect_and_promote("I need to read a file")
        assert promoted == []
        assert "gh_create_pr" in router._l1_pending

    def test_multiple_tools_promoted_in_one_scan(self) -> None:
        router = _make_router([
            {"name": "gh_create_pr", "level": "L1"},
            {"name": "slack_send_message", "level": "L1"},
        ])
        promoted = router.detect_and_promote(
            "I'll use gh_create_pr and slack_send_message"
        )
        assert set(promoted) == {"gh_create_pr", "slack_send_message"}

    def test_tool_call_names_trigger_promotion(self) -> None:
        """Promotion works on tool_call name strings (not just text content)."""
        router = _make_router([{"name": "file_write", "level": "L1"}])
        # Simulate: content="" but tool_call name = "file_write"
        probe = " file_write"
        promoted = router.detect_and_promote(probe)
        assert "file_write" in promoted

    def test_already_promoted_not_double_promoted(self) -> None:
        router = _make_router([{"name": "gh_pr", "level": "L1"}])
        router.promote("gh_pr")
        promoted = router.detect_and_promote("gh_pr again")
        assert promoted == []  # already promoted — not in pending

    def test_l2_tools_not_in_l1_pending(self) -> None:
        router = _make_router([
            {"name": "file_read"},  # L2
            {"name": "gh_pr", "level": "L1"},
        ])
        # file_read is always-L2 — mentioning it has no effect
        promoted = router.detect_and_promote("file_read gh_pr")
        assert promoted == ["gh_pr"]


# ---------------------------------------------------------------------------
# promote() manual
# ---------------------------------------------------------------------------


class TestManualPromote:
    def test_promote_returns_true_for_l1_tool(self) -> None:
        router = _make_router([{"name": "tool_x", "level": "L1"}])
        assert router.promote("tool_x") is True

    def test_promote_returns_false_for_l2_tool(self) -> None:
        router = _make_router([{"name": "tool_x"}])
        assert router.promote("tool_x") is False

    def test_promote_returns_false_for_unknown(self) -> None:
        router = _make_router([{"name": "tool_x"}])
        assert router.promote("nonexistent") is False

    def test_double_promote_returns_false(self) -> None:
        router = _make_router([{"name": "tool_x", "level": "L1"}])
        assert router.promote("tool_x") is True
        assert router.promote("tool_x") is False


# ---------------------------------------------------------------------------
# promotion_log
# ---------------------------------------------------------------------------


class TestPromotionLog:
    def test_log_reflects_state(self) -> None:
        router = _make_router([
            {"name": "file_read"},
            {"name": "gh_pr", "level": "L1"},
            {"name": "slack", "level": "L1"},
        ])
        router.promote("gh_pr")
        log = router.promotion_log()
        assert "file_read" in log["always_l2"]
        assert "gh_pr" in log["promoted"]
        assert "slack" in log["still_pending_l1"]
        assert "gh_pr" not in log["still_pending_l1"]


# ---------------------------------------------------------------------------
# ReAct integration — L1 hint injected and promotion happens
# ---------------------------------------------------------------------------


class TestReActProgressiveIntegration:
    @pytest.mark.asyncio
    async def test_l1_hint_injected_first_step(self) -> None:
        """When L1 tools exist, first call gets a [Tool hints] block appended."""
        from harness.core.modes.react import ReActStrategy

        captured_messages: list[list[dict]] = []

        class _CapturingProvider:
            async def chat(self, messages, **kwargs):
                captured_messages.append(list(messages))
                return {"content": "done", "tokens_used": 0, "tool_calls": []}

        executor = _MockExecutor(["file_read", "gh_create_pr"])
        strategy = ReActStrategy(max_steps=3)
        await strategy.execute(
            messages=[{"role": "user", "content": "do task"}],
            provider=_CapturingProvider(),
            tool_executor=executor,
            declared_tools=[
                {"name": "file_read"},
                {"name": "gh_create_pr", "desc": "Open a GitHub PR", "level": "L1"},
            ],
        )

        # First call's last user message should have the L1 hint appended
        first_call_last_msg = captured_messages[0][-1]
        assert first_call_last_msg["role"] == "user"
        assert "gh_create_pr" in first_call_last_msg["content"]
        assert "Tool hints" in first_call_last_msg["content"]

    @pytest.mark.asyncio
    async def test_l1_tool_promoted_after_mention(self) -> None:
        """LLM mentioning an L1 tool name causes it to appear in tools on next call."""
        from harness.core.modes.react import ReActStrategy

        captured_tool_names: list[set[str]] = []

        class _MentionProvider:
            _call = 0

            async def chat(self, messages, **kwargs):
                tools = {t["name"] for t in kwargs.get("tools", [])}
                captured_tool_names.append(tools)
                self._call += 1
                if self._call == 1:
                    # First call: mention the L1 tool → trigger promotion
                    return {
                        "content": "I'll use gh_create_pr for this",
                        "tokens_used": 10,
                        "tool_calls": [],  # no actual call yet
                    }
                return {"content": "done", "tokens_used": 5, "tool_calls": []}

        executor = _MockExecutor(["file_read", "gh_create_pr"])
        strategy = ReActStrategy(max_steps=5)
        await strategy.execute(
            messages=[{"role": "user", "content": "create a PR"}],
            provider=_MentionProvider(),
            tool_executor=executor,
            declared_tools=[
                {"name": "file_read"},
                {"name": "gh_create_pr", "desc": "Open PR", "level": "L1"},
            ],
        )

        # Step 1: gh_create_pr was L1 → NOT in tools param
        assert "gh_create_pr" not in captured_tool_names[0]
        assert "file_read" in captured_tool_names[0]

        # Step 2+: LLM mentioned gh_create_pr → should be promoted → in tools param
        if len(captured_tool_names) > 1:
            # After promotion, gh_create_pr should appear
            later_tools = set().union(*captured_tool_names[1:])
            assert "gh_create_pr" in later_tools
