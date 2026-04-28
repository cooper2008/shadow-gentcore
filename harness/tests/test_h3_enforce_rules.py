"""Tests for H3 — rule enforcement via set_rule_context (default ON).

The P0 finding from FRAMEWORK_AUDIT_2026Q2: `ToolExecutor.set_rule_context`
was defined but NEVER called in production. Layers 2–6 of RuleEngine (category
overrides, domain policy, agent permissions, workflow/runtime overrides) were
dead. After H3 (enforcement now ON by default):

  - AgentRunner.run() populates ToolExecutor.set_rule_context(RuleContext(...))
    before the execution strategy runs tools unless GENTCORE_UNSAFE_DISABLE_RULES
    is set to a truthy value (1/true/yes/on).
  - Rule context is CLEARED after each run so one agent's permissions
    don't leak into another sharing the same ToolExecutor instance.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from harness.core.agent_runner import AgentRunner, build_rule_context_from_manifest
from harness.core.rule_engine import RuleContext, RuleEngine
from harness.core.tool_executor import ToolExecutor


class _StubProvider:
    """Deterministic provider — returns a canned result without network."""

    async def chat(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        return {"content": '{"ok": true}', "tokens_used": 10, "tool_calls": []}


class _StubStrategy:
    """Captures the rule_context state at the moment strategy.execute runs."""

    def __init__(self) -> None:
        self.captured_context: RuleContext | None = None
        self.tool_executor: ToolExecutor | None = None

    async def execute(self, messages, provider, tool_executor=None, **kwargs):  # type: ignore[no-untyped-def]
        self.tool_executor = tool_executor
        if tool_executor is not None:
            self.captured_context = getattr(tool_executor, "_rule_context", None)
        return {"content": '{"ok": true}', "tokens_used": 1, "tool_calls": []}


class _StubModeDispatcher:
    def __init__(self, strategy: _StubStrategy) -> None:
        self.strategy = strategy

    def dispatch(self, execution_mode):  # type: ignore[no-untyped-def]
        return self.strategy


def _manifest(category: str, shell_command_perm: str = "allow", file_edit_perm: str = "deny") -> dict:
    return {
        "id": f"test/_runner_test/{category}/v1",
        "domain": "test",
        "category": category,
        "system_prompt_ref": "system_prompt.md",
        "permissions": {
            "shell_command": shell_command_perm,
            "file_edit": file_edit_perm,
            "file_create": "deny",
            "network_access": "deny",
        },
        "tools": [],
    }


class TestBuildRuleContextFromManifest:
    def test_extracts_category_and_permissions(self) -> None:
        ctx = build_rule_context_from_manifest(_manifest("ops"))
        assert isinstance(ctx, RuleContext)
        assert ctx.agent_category == "ops"
        assert ctx.agent_permissions.get("shell_command") == "allow"
        assert ctx.agent_permissions.get("file_edit") == "deny"

    def test_handles_missing_permissions_block(self) -> None:
        ctx = build_rule_context_from_manifest({
            "id": "x",
            "domain": "test",
            "category": "reasoning",
            "system_prompt_ref": "s.md",
        })
        assert ctx.agent_category == "reasoning"
        assert ctx.agent_permissions == {}

    def test_handles_missing_category(self) -> None:
        ctx = build_rule_context_from_manifest({
            "id": "x",
            "domain": "test",
            "system_prompt_ref": "s.md",
        })
        assert ctx.agent_category == ""


class TestSafeDefaultRuleEnforcement:
    @pytest.mark.asyncio
    async def test_rule_context_set_when_flag_unset(self) -> None:
        os.environ.pop("GENTCORE_ENFORCE_RULES", None)
        os.environ.pop("GENTCORE_UNSAFE_DISABLE_RULES", None)
        strategy = _StubStrategy()
        runner = AgentRunner(
            provider=_StubProvider(),
            mode_dispatcher=_StubModeDispatcher(strategy),
            tool_executor=ToolExecutor(),
        )
        await runner.run(manifest=_manifest("ops"))
        assert strategy.captured_context is not None
        assert strategy.captured_context.agent_category == "ops"

    @pytest.mark.asyncio
    async def test_legacy_disable_flag_no_longer_disables_rules(self) -> None:
        with patch.dict(os.environ, {"GENTCORE_ENFORCE_RULES": "0"}, clear=False):
            strategy = _StubStrategy()
            runner = AgentRunner(
                provider=_StubProvider(),
                mode_dispatcher=_StubModeDispatcher(strategy),
                tool_executor=ToolExecutor(),
            )
            await runner.run(manifest=_manifest("ops"))
            assert strategy.captured_context is not None

    @pytest.mark.asyncio
    async def test_explicit_unsafe_disable_flag_bypasses_rule_context(self) -> None:
        with patch.dict(os.environ, {"GENTCORE_UNSAFE_DISABLE_RULES": "1"}, clear=False):
            strategy = _StubStrategy()
            runner = AgentRunner(
                provider=_StubProvider(),
                mode_dispatcher=_StubModeDispatcher(strategy),
                tool_executor=ToolExecutor(),
            )
            await runner.run(manifest=_manifest("ops"))
            assert strategy.captured_context is None


class TestFlagOnPopulatesRuleContext:
    @pytest.mark.asyncio
    async def test_rule_context_carries_manifest_category(self) -> None:
        with patch.dict(os.environ, {"GENTCORE_ENFORCE_RULES": "1"}, clear=False):
            strategy = _StubStrategy()
            runner = AgentRunner(
                provider=_StubProvider(),
                mode_dispatcher=_StubModeDispatcher(strategy),
                tool_executor=ToolExecutor(),
            )
            await runner.run(manifest=_manifest("ops"))
            assert strategy.captured_context is not None
            assert strategy.captured_context.agent_category == "ops"

    @pytest.mark.asyncio
    async def test_rule_context_carries_manifest_permissions(self) -> None:
        with patch.dict(os.environ, {"GENTCORE_ENFORCE_RULES": "1"}, clear=False):
            strategy = _StubStrategy()
            runner = AgentRunner(
                provider=_StubProvider(),
                mode_dispatcher=_StubModeDispatcher(strategy),
                tool_executor=ToolExecutor(),
            )
            await runner.run(
                manifest=_manifest("reasoning", shell_command_perm="allow"),
            )
            assert strategy.captured_context is not None
            assert strategy.captured_context.agent_permissions.get("shell_command") == "allow"


class TestLiveEnforcementBehaviour:
    @pytest.mark.asyncio
    async def test_ops_agent_can_run_shell_under_enforcement(self) -> None:
        """S3 + H3 combined: re-tagged ops agent's shell remains allowed when rules enforce."""
        with patch.dict(os.environ, {"GENTCORE_ENFORCE_RULES": "1"}, clear=False):
            rules = RuleEngine()
            executor = ToolExecutor(rule_engine=rules)
            strategy = _StubStrategy()
            runner = AgentRunner(
                provider=_StubProvider(),
                mode_dispatcher=_StubModeDispatcher(strategy),
                tool_executor=executor,
            )
            await runner.run(manifest=_manifest("ops"))
            # Context must be live; simulate a tool call via the rule engine
            ctx = strategy.captured_context
            assert ctx is not None
            decision = rules.check_tool_call("shell_exec", {"command": "echo hi"}, ctx)
            assert decision.allowed or decision.decision.value == "ask", (
                f"ops agent's shell_exec should not be denied under H3 enforcement, "
                f"got {decision.decision.value} ({decision.reason})"
            )

    @pytest.mark.asyncio
    async def test_reasoning_agent_shell_is_denied_under_enforcement(self) -> None:
        """Before S3, reasoning+shell_command:allow agents silently passed. After
        H3, they are correctly denied at the merge layer (motivating the S3 retag)."""
        with patch.dict(os.environ, {"GENTCORE_ENFORCE_RULES": "1"}, clear=False):
            rules = RuleEngine()
            executor = ToolExecutor(rule_engine=rules)
            strategy = _StubStrategy()
            runner = AgentRunner(
                provider=_StubProvider(),
                mode_dispatcher=_StubModeDispatcher(strategy),
                tool_executor=executor,
            )
            # Unretagged manifest: reasoning + shell_command: allow
            await runner.run(manifest=_manifest("reasoning", shell_command_perm="allow"))
            ctx = strategy.captured_context
            assert ctx is not None
            decision = rules.check_tool_call("shell_exec", {"command": "echo hi"}, ctx)
            assert decision.decision.value == "deny", (
                "reasoning category's shell_command: deny override must now win against "
                "agent permissions under H3 enforcement"
            )


class TestRuleContextIsolation:
    @pytest.mark.asyncio
    async def test_context_cleared_after_run(self) -> None:
        """One agent's permissions must not leak into the next agent's run
        when a ToolExecutor is shared across agents."""
        with patch.dict(os.environ, {"GENTCORE_ENFORCE_RULES": "1"}, clear=False):
            executor = ToolExecutor()
            strategy = _StubStrategy()
            runner = AgentRunner(
                provider=_StubProvider(),
                mode_dispatcher=_StubModeDispatcher(strategy),
                tool_executor=executor,
            )
            await runner.run(manifest=_manifest("ops"))
            # After run, executor's rule_context should be cleared
            assert executor._rule_context is None, (
                "ToolExecutor.rule_context must be cleared after each run to "
                "prevent category leak across agents"
            )
