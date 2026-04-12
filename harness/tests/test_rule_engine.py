"""Tests for RuleEngine — platform rules, permission merging, hot-reload, audit."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
import yaml

from harness.core.rule_engine import RuleEngine, RuleContext, RuleDecision, Decision
from harness.core.tool_executor import ToolExecutor
from harness.tools.builtin import register_builtins


class TestPlatformRules:
    """Platform rules (Layer 1) — non-negotiable, cannot be overridden."""

    def test_blocks_rm_rf(self) -> None:
        engine = RuleEngine()
        d = engine.check_tool_call("shell_exec", {"command": "rm -rf /"})
        assert d.denied
        assert "Blocked command" in d.reason

    def test_blocks_fork_bomb(self) -> None:
        engine = RuleEngine()
        d = engine.check_tool_call("shell_exec", {"command": ":(){ :|:& };:"})
        assert d.denied

    def test_allows_safe_commands(self) -> None:
        engine = RuleEngine()
        d = engine.check_tool_call("shell_exec", {"command": "pytest tests/ -v"})
        # May return ASK (default) but not DENY
        assert not d.denied or d.rule_layer != "platform"

    def test_blocks_eval_in_file_write(self) -> None:
        engine = RuleEngine()
        d = engine.check_tool_call("file_write", {"content": 'result = eval("2+2")', "path": "test.py"})
        assert d.denied
        assert "blocked pattern" in d.reason.lower()

    def test_allows_clean_file_write(self) -> None:
        engine = RuleEngine()
        d = engine.check_tool_call("file_write", {"content": "def hello(): return 42", "path": "test.py"})
        # Should not be blocked by platform rules
        assert d.rule_layer != "platform" or d.allowed

    def test_blocks_oversized_file(self) -> None:
        engine = RuleEngine()
        huge_content = "x" * (11 * 1024 * 1024)  # 11MB > 10MB default limit
        d = engine.check_tool_call("file_write", {"content": huge_content, "path": "big.txt"})
        assert d.denied
        assert "max size" in d.reason.lower()


class TestContentCheck:
    def test_blocks_api_key_in_content(self) -> None:
        engine = RuleEngine()
        d = engine.check_content('api_key = "sk-12345678901234567890"')
        assert d.denied

    def test_blocks_private_key(self) -> None:
        engine = RuleEngine()
        d = engine.check_content("-----BEGIN RSA PRIVATE KEY-----")
        assert d.denied

    def test_allows_clean_content(self) -> None:
        engine = RuleEngine()
        d = engine.check_content("def hello(): return 42")
        assert d.allowed


class TestPathCheck:
    def test_blocks_env_file(self) -> None:
        engine = RuleEngine()
        d = engine.check_path(".env")
        assert d.denied

    def test_blocks_secrets_dir(self) -> None:
        engine = RuleEngine()
        d = engine.check_path("secrets/api_key.txt")
        assert d.denied

    def test_allows_src_path(self) -> None:
        engine = RuleEngine()
        d = engine.check_path("src/main.py")
        assert d.allowed

    def test_domain_allowed_paths(self) -> None:
        engine = RuleEngine()
        ctx = RuleContext(domain_policy={"allowed_paths": ["src/", "tests/"]})
        d = engine.check_path("config/secret.yaml", ctx)
        assert d.denied


class TestPermissionMerging:
    """Layers 2-6: category, domain, agent, workflow, runtime — most restrictive wins."""

    def test_default_permissions(self) -> None:
        engine = RuleEngine()
        d = engine.check_tool_call("file_read", {"path": "test.py"})
        assert d.allowed  # file_read defaults to allow

    def test_category_override(self) -> None:
        engine = RuleEngine()
        ctx = RuleContext(agent_category="reasoning")
        d = engine.check_tool_call("file_write", {"path": "test.py", "content": "x"}, ctx)
        # reasoning category sets file_write to deny
        assert d.denied

    def test_agent_permission_more_restrictive(self) -> None:
        engine = RuleEngine()
        ctx = RuleContext(
            agent_category="fast-codegen",  # file_write: allow
            agent_permissions={"file_write": "deny"},  # more restrictive
        )
        d = engine.check_tool_call("file_write", {"path": "test.py", "content": "x"}, ctx)
        assert d.denied

    def test_most_restrictive_wins(self) -> None:
        """If any layer says deny, the result is deny."""
        engine = RuleEngine()
        ctx = RuleContext(
            agent_category="fast-codegen",         # shell_command: ask
            agent_permissions={"shell_command": "allow"},  # less restrictive
            domain_policy={"permissions": {"shell_command": "deny"}},  # most restrictive
        )
        d = engine.check_tool_call("shell_exec", {"command": "echo hi"}, ctx)
        assert d.denied

    def test_all_allow_results_in_allow(self) -> None:
        engine = RuleEngine()
        ctx = RuleContext(
            agent_category="fast-codegen",
            agent_permissions={"file_write": "allow"},
        )
        d = engine.check_tool_call("file_write", {"path": "test.py", "content": "x"}, ctx)
        assert d.allowed


class TestHotReload:
    def test_reloads_on_file_change(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"platform": {"blocked_commands": []}, "defaults": {"shell_command": "allow"}}, f)
            f.flush()

            engine = RuleEngine(f.name)
            d = engine.check_tool_call("shell_exec", {"command": "echo hi"})
            assert d.allowed

            # Change the file to deny shell_command
            time.sleep(0.05)  # ensure mtime changes
            Path(f.name).write_text(yaml.dump({
                "platform": {"blocked_commands": []},
                "defaults": {"shell_command": "deny"},
            }))

            d = engine.check_tool_call("shell_exec", {"command": "echo hi"})
            assert d.denied


class TestAuditLog:
    def test_records_all_decisions(self) -> None:
        engine = RuleEngine()
        engine.check_tool_call("file_read", {"path": "test.py"})
        engine.check_tool_call("shell_exec", {"command": "rm -rf /"})
        engine.check_tool_call("file_write", {"content": "ok", "path": "x.py"})

        assert len(engine.audit_log) == 3
        assert engine.audit_log[0]["tool_name"] == "file_read"
        assert engine.audit_log[1]["decision"] == "deny"  # blocked command

    def test_clear_audit_log(self) -> None:
        engine = RuleEngine()
        engine.check_tool_call("file_read", {"path": "test.py"})
        engine.clear_audit_log()
        assert len(engine.audit_log) == 0


class TestToolExecutorWithRules:
    """ToolExecutor blocks tool calls when RuleEngine denies them."""

    @pytest.mark.asyncio
    async def test_blocks_dangerous_command(self) -> None:
        engine = RuleEngine()
        executor = ToolExecutor(rule_engine=engine)
        register_builtins(executor)

        result = await executor.execute({
            "id": "call_1",
            "name": "shell_exec",
            "arguments": {"command": "rm -rf /"},
        })
        assert result["success"] is False
        assert result.get("blocked_by_rule") is True
        assert "Blocked" in result["output"]

    @pytest.mark.asyncio
    async def test_allows_safe_command(self) -> None:
        engine = RuleEngine()
        executor = ToolExecutor(rule_engine=engine)
        register_builtins(executor)

        result = await executor.execute({
            "id": "call_2",
            "name": "shell_exec",
            "arguments": {"command": "echo safe"},
        })
        # shell_command defaults to "ask" which is not "deny"
        # The ToolExecutor only blocks on DENY, not ASK
        assert "Blocked" not in result.get("output", "")

    @pytest.mark.asyncio
    async def test_executor_without_rules_still_works(self) -> None:
        executor = ToolExecutor()  # no rule_engine
        register_builtins(executor)

        result = await executor.execute({
            "id": "call_3",
            "name": "shell_exec",
            "arguments": {"command": "echo works"},
        })
        assert result["success"] is True
