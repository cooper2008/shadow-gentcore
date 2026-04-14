"""Tests for optional code hooks in agent manifests (hooks_ref feature)."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from harness.core.manifest_loader import ManifestLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_agent(
    agent_dir: Path,
    manifest_extra: dict[str, Any] | None = None,
    hooks_src: str | None = None,
) -> None:
    """Write a minimal agent directory used by several tests."""
    agent_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"id": "test_agent", "version": "1.0.0"}
    if manifest_extra:
        manifest.update(manifest_extra)
    (agent_dir / "agent_manifest.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
    (agent_dir / "system_prompt.md").write_text("You are a test agent.", encoding="utf-8")
    if hooks_src is not None:
        (agent_dir / "hooks.py").write_text(textwrap.dedent(hooks_src), encoding="utf-8")


# ---------------------------------------------------------------------------
# ManifestLoader.load_agent tests
# ---------------------------------------------------------------------------

class TestLoadAgentHooks:
    def test_no_hooks_ref_returns_empty_hooks(self, tmp_path: Path) -> None:
        """Agent without hooks_ref has _hooks = {} — no regression."""
        agent_dir = tmp_path / "agent_no_hooks"
        _write_agent(agent_dir)
        loader = ManifestLoader()
        manifest, system_prompt, context_items = loader.load_agent(agent_dir)
        assert manifest["_hooks"] == {}
        assert system_prompt == "You are a test agent."
        assert context_items == []

    def test_hooks_ref_loads_pre_execute(self, tmp_path: Path) -> None:
        """pre_execute hook is loaded from hooks.py."""
        hooks_src = """
            def pre_execute(manifest, task, context_items):
                context_items.append({"source": "hook", "content": "injected"})
                return context_items
        """
        agent_dir = tmp_path / "agent_pre"
        _write_agent(agent_dir, {"hooks_ref": "hooks.py"}, hooks_src)
        loader = ManifestLoader()
        manifest, _, _ = loader.load_agent(agent_dir)
        assert "pre_execute" in manifest["_hooks"]
        assert callable(manifest["_hooks"]["pre_execute"])

    def test_hooks_ref_loads_post_execute(self, tmp_path: Path) -> None:
        """post_execute hook is loaded from hooks.py."""
        hooks_src = """
            def post_execute(manifest, task, result):
                result["hook_ran"] = True
                return result
        """
        agent_dir = tmp_path / "agent_post"
        _write_agent(agent_dir, {"hooks_ref": "hooks.py"}, hooks_src)
        loader = ManifestLoader()
        manifest, _, _ = loader.load_agent(agent_dir)
        assert "post_execute" in manifest["_hooks"]
        assert callable(manifest["_hooks"]["post_execute"])

    def test_hooks_ref_loads_pre_tool_call(self, tmp_path: Path) -> None:
        """pre_tool_call hook is loaded when present."""
        hooks_src = """
            def pre_tool_call(manifest, tool_name, tool_args):
                return tool_args
        """
        agent_dir = tmp_path / "agent_pretool"
        _write_agent(agent_dir, {"hooks_ref": "hooks.py"}, hooks_src)
        loader = ManifestLoader()
        manifest, _, _ = loader.load_agent(agent_dir)
        assert "pre_tool_call" in manifest["_hooks"]

    def test_missing_hooks_file_logs_warning_no_crash(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """hooks_ref pointing to non-existent file logs a warning and does not crash."""
        import logging
        agent_dir = tmp_path / "agent_missing_hooks"
        _write_agent(agent_dir, {"hooks_ref": "hooks.py"})
        # hooks.py intentionally NOT written
        loader = ManifestLoader()
        with caplog.at_level(logging.WARNING, logger="harness.core.manifest_loader"):
            manifest, _, _ = loader.load_agent(agent_dir)
        assert manifest["_hooks"] == {}
        assert any("hooks_ref" in rec.message and "not found" in rec.message for rec in caplog.records)

    def test_partial_hooks_only_pre_execute(self, tmp_path: Path) -> None:
        """hooks.py with only pre_execute — post_execute absent, no KeyError."""
        hooks_src = """
            def pre_execute(manifest, task, context_items):
                return context_items
        """
        agent_dir = tmp_path / "agent_partial"
        _write_agent(agent_dir, {"hooks_ref": "hooks.py"}, hooks_src)
        loader = ManifestLoader()
        manifest, _, _ = loader.load_agent(agent_dir)
        assert "pre_execute" in manifest["_hooks"]
        assert "post_execute" not in manifest["_hooks"]
        assert "pre_tool_call" not in manifest["_hooks"]


# ---------------------------------------------------------------------------
# AgentRunner integration tests
# ---------------------------------------------------------------------------

class TestAgentRunnerHooks:
    """Verify hooks are actually invoked during AgentRunner.run()."""

    def _make_runner(self) -> Any:
        from harness.core.agent_runner import AgentRunner
        provider = MagicMock()
        runner = AgentRunner(provider=provider)
        return runner

    def _make_strategy(self, content: str = "hello") -> MagicMock:
        strategy = MagicMock()
        strategy.execute = AsyncMock(
            return_value={"content": content, "tokens_used": 10}
        )
        return strategy

    @pytest.mark.asyncio
    async def test_pre_execute_hook_modifies_context(self, tmp_path: Path) -> None:
        """pre_execute hook can append to context_items before prompt assembly."""
        called_with: list[Any] = []

        def pre_execute(manifest: dict, task: dict, context_items: list) -> list:
            called_with.append(list(context_items))
            context_items.append({"source": "hook", "content": "extra context"})
            return context_items

        manifest = {"id": "hook_agent", "version": "1.0.0", "_hooks": {"pre_execute": pre_execute}}
        runner = self._make_runner()
        strategy = self._make_strategy()

        with patch.object(runner.mode_dispatcher, "dispatch", return_value=strategy):
            result = await runner.run(
                manifest=manifest,
                task={"task_id": "t1"},
                system_prompt_content="test",
                context_items=[{"source": "base", "content": "base context"}],
            )

        assert result["status"] == "completed"
        assert len(called_with) == 1
        # pre_execute received the original context_items
        assert called_with[0] == [{"source": "base", "content": "base context"}]

    @pytest.mark.asyncio
    async def test_post_execute_hook_transforms_result(self, tmp_path: Path) -> None:
        """post_execute hook can modify the result dict returned by run()."""

        def post_execute(manifest: dict, task: dict, result: dict) -> dict:
            result["hook_applied"] = True
            result["content"] = "transformed"
            return result

        manifest = {"id": "hook_agent", "version": "1.0.0", "_hooks": {"post_execute": post_execute}}
        runner = self._make_runner()
        strategy = self._make_strategy("original content")

        with patch.object(runner.mode_dispatcher, "dispatch", return_value=strategy):
            out = await runner.run(
                manifest=manifest,
                task={"task_id": "t2"},
                system_prompt_content="test",
            )

        assert out["status"] == "completed"
        assert out["result"]["hook_applied"] is True
        assert out["result"]["content"] == "transformed"

    @pytest.mark.asyncio
    async def test_no_hooks_agent_unaffected(self) -> None:
        """Agent with _hooks={} runs exactly as before — no regression."""
        manifest = {"id": "plain_agent", "version": "1.0.0", "_hooks": {}}
        runner = self._make_runner()
        strategy = self._make_strategy("plain result")

        with patch.object(runner.mode_dispatcher, "dispatch", return_value=strategy):
            out = await runner.run(
                manifest=manifest,
                task={"task_id": "t3"},
                system_prompt_content="test",
            )

        assert out["status"] == "completed"
        assert out["content"] == "plain result"

    @pytest.mark.asyncio
    async def test_manifest_without_hooks_key_unaffected(self) -> None:
        """Agent manifest dict without _hooks key at all is safe (no KeyError)."""
        manifest = {"id": "plain_agent", "version": "1.0.0"}
        runner = self._make_runner()
        strategy = self._make_strategy("result")

        with patch.object(runner.mode_dispatcher, "dispatch", return_value=strategy):
            out = await runner.run(
                manifest=manifest,
                task={"task_id": "t4"},
                system_prompt_content="test",
            )

        assert out["status"] == "completed"
